#!/usr/bin/python3

import sys
sys.path.append('/usr/local/share/micropython')

from os import listdir, makedirs, remove
#from os.path import exists, isdir, isfile, getsize, basename, splitext, dirname, realpath
from os.path import exists, isdir, isfile, getsize, basename, dirname
from hashlib import sha256
#from difflib import unified_diff
import sqlite3
import tarfile
import random
import string
from sys import stderr
import json
import logging

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

PREFIX = '/PROV'

PROVCFG = "config.json"
SECRET  = "secret"

LOCAL_PATH              = PREFIX + '/PROV/'
#LOCAL_PATH_SETS        = LOCAL_PATH + "sets/" # only used if sets are stored on filesystem instead of database
LOCAL_PATH_PROVCFG      = LOCAL_PATH + PROVCFG
LOCAL_PATH_DB           = LOCAL_PATH + "sets.db"
LOCAL_PATH_SECRET       = LOCAL_PATH + SECRET

IN_PATH             = PREFIX + "/IMPORT/"
#IN_PATH_SETS        = IN_PATH + "sets/" # trailing slash required!
#IN_PATH_PROVCFG     = IN_PATH + PROVCFG
#IN_PATH_PROVCA      = IN_PATH + "prov.ca"


OUT_PATH            = PREFIX + "/EXPORT/"

INODES_IGNORE = ['lost+found'] # TODO: add other system specific inodes, e.g. i remember macosx has quite a few of those being created automatically

try:
    os_info = {}
    with open("/etc/os-release") as f:
        for line in f:
            k,v = line.rstrip().split("=", 1)
            os_info[k] = v.strip('"').strip("'")
except:
    log.error("Can't determine OS", file=stderr)
    os_info['NAME'] = os_info['ID'] = os_info['VERSION'] = os_info['VERSION_ID'] = 'UNKNOWN'


class SQLConn:
    def __init__(self):
        self.sql_conn = None
        self.connected = False

    def connect(self):
        self.sql_conn = sqlite3.connect(LOCAL_PATH_DB)
        self.sql_conn.isolation_level = None
        self.sql_conn.row_factory = sqlite3.Row
        self.sql_cur = self.sql_conn.cursor()
        self.connected = True

    def execute(self, query, values = (), commit = True):
        if not self.connected:
            self.connect()
        log.debug("SQL query to be executed (query/values): {} / {}".format(query, values))
        self.sql_cur.execute(query, values)
        if commit:
            self.sql_conn.commit()

    def begin(self):
        self.sql_cur.execute("BEGIN")

    def rollback(self):
        self.sql_cur.execute("ROLLBACK")

    def commit(self):
        self.sql_cur.execute("COMMIT")

    def fetchone(self):
        return self.sql_cur.fetchone()

    def fetchall(self):
        return self.sql_cur.fetchall()

    def rows_affected(self):
        return self.sql_cur.rowcount

    def __del__(self):
        try:
            self.sql_conn.close()
        except:
            pass


class ProvSysError(Exception):
    """Basic Exception for handling errors with the provisioning system itself"""
    def __init__(self, msg=None, verb=None):
        if not msg:
            msg = "UNKNOWN ({})".format(self.__class__.__name__)
        self.msg = msg
        self.verb = verb
        if verb:
            super(ProvSysError, self).__init__(msg, verb)
        else:
            super(ProvSysError, self).__init__(msg)

class LocalIntegrityError(ProvSysError):
    """error on server side"""

class IncomingIntegrityError(ProvSysError):
    """to be imported data incompatible"""

class Uninitialized(ProvSysError):
    """not yet initialized"""

class NoFree(ProvSysError):
    """all sets are in use"""


class ProvSystem():
    def __init__(self):
        # as we'd like to create an SQL connection right at the beginning globally, we need to ensure the directory the sqlite file is stored in is setup before. this is redundant and ugly.
        if not exists(LOCAL_PATH):
            makedirs(LOCAL_PATH)
        self.sql = SQLConn()
        self.cfg = {}

        #if not exists(LOCAL_PATH_IMPORTED):
        #    with open(LOCAL_PATH_IMPORTED, "w") as f: f.write("")

    def read_file(self, path, binary = False):
        return open("%s" % (path), binary and "rb" or "r").read()

    def parse_config(self):
        self.cfg = json.load(open(LOCAL_PATH_PROVCFG, 'r'))

    def get_batches(self, check_for_imported=True):
        """Get all batches, meaning, everything which *might* be one.
        To ensure they're valid and/or compatible, call:
            `check_consistency_incoming_batch()`
        and/or
            `check_compatibility_incoming_batch()`
        ."""

        #return [elem for elem in listdir(IN_PATH) if (isdir("%s%s" % (IN_PATH, elem))) and (elem != "KEEP")]
        #return [{'name': (elem for elem in listdir(IN_PATH) if elem != "KEEP"), 'imported': False}]
        return [{'name': elem, 'imported': self.is_batch_already_imported(elem) if check_for_imported else None} for elem in listdir(IN_PATH) if not elem in INODES_IGNORE]

    def get_backups(self):
        return [elem for elem in listdir(OUT_PATH) if elem not in INODES_IGNORE]

    def _non_empty_dir(self, path, exc_cls = Exception):
        if not (isdir(path) and len(listdir(path))):
            raise exc_cls("{}: expected to be an existing, non-empty directory".format(path))
    def _non_empty_file(self, path, exc_cls = Exception):
        if not (isfile(path) and getsize(path)):
            raise exc_cls("{}: expected to be an existing, non-empty, regular file".format(path))

    def get_project_name(self):
        return self.cfg['project']

    def get_dynamic_files(self):
        files = []
        for endpoint_key, endpoint_val in self.cfg['endpoints'].items():
            if 'files' in endpoint_val:
                for file1 in endpoint_val['files']:
                    if file1['type'] == 'dynamic':
                        files.append("{}.{}".format(endpoint_key, file1['name']) if 'name' in file1 else endpoint_key)
        return files

    def get_static_files(self):
        files = []
        for endpoint_key, endpoint_val in self.cfg['endpoints'].items():
            if 'files' in endpoint_val:
                for file1 in endpoint_val['files']:
                    if file1['type'] == 'static':
                        files.append("{}.{}".format(endpoint_key, file1['name']) if 'name' in file1 else endpoint_key)
        return files

    def check_consistency_incoming_generic(self):
        # THIS METHOD IS OBSOLETE!

        # although this method would throw an exception if it fails, we still
        # need to return True if it doesn't, to make logical constructs like
        # `check_X() or False` work (as `None` -> `False`).
        return True

    def check_consistency_incoming_batch(self, batch):
        try:
            tar_fd = tarfile.open(IN_PATH + batch, 'r')
            tar_members = [ mem.strip('./') for mem in tar_fd.getnames() ]
            assert(PROVCFG in tar_members)
            assert("sets" in tar_members)
        except:
            raise IncomingIntegrityError("{}: expected it being a stage2 provisioning import archive".format(IN_PATH + batch))

        # although this method would throw an exception if it fails, we still
        # need to return True if it doesn't, to make logical constructs like
        # `check_X() or False` work (as `None` -> `False`).
        return True

    def check_consistency_incoming_batches(self):
        batches = self.get_batches(check_for_imported=False)
        for batch in batches:
            self.check_consistency_incoming_batch(batch['name'])

    def check_consistency_incoming(self):
        #self.check_consistency_incoming_generic()
        self.check_consistency_incoming_batches()

        # although this method would throw an exception if it fails, we still
        # need to return True if it doesn't, to make logical constructs like
        # `check_X() or False` work (as `None` -> `False`).
        return True

    def check_consistency_local(self):

        def check_config_exists():
            try:
                self._non_empty_file(LOCAL_PATH_PROVCFG, ProvSysError)
            except:
                return LocalIntegrityError("{}: expected it being a non-empty file".format(LOCAL_PATH_PROVCFG))

        def check_config_parsable():
            try:
                self.parse_config()
            except:
                return LocalIntegrityError("{}: expected it being a JSON-parsable file".format(LOCAL_PATH_PROVCFG))

        def check_db_file():
            try:
                self._non_empty_file(LOCAL_PATH_DB, ProvSysError)
                #TODO: read and parse sqlite file
            except:
                return LocalIntegrityError("{}: expected it being a non-empty, sqlite database file".format(LOCAL_PATH_DB))

        def check_files_db():
            try:
                self.sql.execute('PRAGMA table_info(sets)')
                columns = [i[1] for i in self.sql.fetchall()]
                dyn_files = self.get_dynamic_files()
                for dyn_file in dyn_files:
                    if not dyn_file in columns:
                        raise LocalIntegrityError("{}: no such column in database".format(dyn_file))
            except Exception as exc:
                return exc

        def check_files():
            try:
                for static_file in self.get_static_files():
                    self._non_empty_file("{}/{}".format(LOCAL_PATH, static_file), LocalIntegrityError)
            except Exception as exc:
                return exc

        # we split the config check in order to avoid the following situation:
        # if config file exists but is not parsable (e.g. syntax error) we might
        # end up in an "unprovisioned"-situation if all checks fail.
        res = [
            check_config_exists(),
            check_config_parsable(),
            check_db_file(),
            check_files(),
            check_files_db()
        ]

        if all(isinstance(_res, Exception) for _res in res):
            # all checks resulted in an exception, which means, system is not yet initialized
            return False

        if all(not isinstance(_res, Exception) for _res in res):
            # all checks passed which means, system is initialized and consistent
            return True

        # if not all items are exceptions and not all items are non-exceptions,
        # we reached an inconsistent state.
        # Raising the first exception we encountered.
        raise next( (_res for _res in res if isinstance(_res, Exception)))

    def initialized(self):
        return self.check_consistency_local()

    def check_compatibility_incoming_batch(self, batch):
        if not self.initialized():
            raise Uninitialized()

        with tarfile.open(IN_PATH + batch) as tar_fd:
            prfx = './' if '.' in tar_fd.getnames() else ''
            if not self.diff_cfg(tar_fd.extractfile("{}{}".format(prfx, PROVCFG))):
                raise IncomingIntegrityError("{} vs {}: To be imported provisioning config file incompatible to current one".format(LOCAL_PATH_PROVCFG, "{}|config.json".format(batch), LOCAL_PATH_PROVCFG))

        # although this method would throw an exception if it fails, we still
        # need to return True if it doesn't, to make logical constructs like
        # `check_X() or False` work (as `None` -> `False`).
        return True

    def diff_cfg(self, fd_cfg_incoming):
        return json.dumps(self.cfg, sort_keys=True) == json.dumps(json.loads(fd_cfg_incoming.read().decode('utf-8')), sort_keys=True)

    def initialize_db(self):
        factory_set_fields = {
            'id': ('TEXT', 'PRIMARY KEY', 'UNIQUE', 'NOT NULL'),
            'batch': ('TEXT', 'NOT NULL'),
            'imported_dt': ('DATETIME', 'DEFAULT CURRENT_TIMESTAMP'),
            'downloaded_cnt': ('INTEGER', 'DEFAULT 0'),
            'downloaded_dt': ('DATETIME',),
            'dev_id': ('TEXT', 'UNIQUE'),
            'prod_id': ('TEXT',),
            'fw_ver': ('TEXT',),
            'comment': ('TEXT',),
        }
        dynamic_set_fields = { field: ('BINARY', 'UNIQUE', 'NOT NULL') for field in self.get_dynamic_files() }
        log.debug("Fields to be created: {}".format([ tuple(factory_set_fields.items()) + tuple(dynamic_set_fields.items()) ]))
        self.sql.execute('CREATE TABLE `sets` ( {} )'.format(
            ',\n'.join({ '"{}" {}'.format(col, ' '.join(prop)) for col,prop in {**factory_set_fields, **dynamic_set_fields}.items() })
            #',\n'.join({ '"{}" {}'.format(col, ' '.join(prop)) for col,prop in dict([ tuple(factory_set_fields.items()) + tuple(dynamic_set_fields.items()) ]) })
        ))

    def initialize(self, batch):
        self.check_consistency_incoming_batch(batch)
        if not exists(LOCAL_PATH):
            makedirs(LOCAL_PATH)
        with tarfile.open(IN_PATH + batch) as tar_fd:
            prfx = './' if '.' in tar_fd.getnames() else ''
            with open(LOCAL_PATH_PROVCFG, 'wb') as fd:
                fd.write(tar_fd.extractfile(tar_fd.getmember("{}{}".format(prfx, PROVCFG))).read())
            self.parse_config()
            with open(LOCAL_PATH_SECRET, 'wb') as fd:
                fd.write(tar_fd.extractfile(tar_fd.getmember("{}{}".format(prfx, SECRET))).read())
            for _endpoint_key, _endpoint_val in self.cfg['endpoints'].items():
                for static_file in self.get_static_files():
                    try:
                        member = tar_fd.getmember("{}{}".format(prfx, static_file))
                    except:
                        raise IncomingIntegrityError("File referenced in config but wasn't found in archive", "{}{}".format(prfx, static_file))
                    with open("{}{}".format(LOCAL_PATH, static_file), 'wb') as fd:
                        fd.write(tar_fd.extractfile(member).read())
        self.initialize_db()

    def is_batch_already_imported(self, batch):
        if not self.initialized():
            return False

        self.sql.execute("SELECT `batch` FROM `sets` GROUP BY `batch`")
        set1 = self.sql.fetchall()
        for elem in set1:
            if batch in elem:
                return True
        return False

    #def backup(self, name="PROV-BACKUP_%s" % (''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(5)))):
    def backup(self, name):
        if not self.initialized:
            raise Uninitiaized("Can not backup uninitialized system")

        with tarfile.open(OUT_PATH + name, 'w:gz') as tar_fd:
            tar_fd.add(LOCAL_PATH)

    def import_set(self, set):
        if not self.initialized():
            raise Uninitialized("Before importing single sets, the system needs to be initialized with metadata for a particular provisioning infrastructure")
        raise NotImplemented()

    def reset(self):
        for _endpoint_key, _endpoint_val in self.cfg['endpoints'].items():
            for static_file in self.get_static_files():
                try:
                    remove("{}{}".format(LOCAL_PATH, static_file))
                except:
                    pass
        try:
            remove(LOCAL_PATH_PROVCFG)
        except:
            pass
        try:
            remove(LOCAL_PATH_SECRET)
        except:
            pass
        try:
            remove(LOCAL_PATH_DB)
        except:
            pass

    def import_batch(self, batch):
        self.check_consistency_incoming_batch(batch)
        if not self.initialized():
            try:
                self.initialize(batch)
            except:
                self.reset()
                raise # raise original exception to inform frontend about initial reason of failure
        else:
            self.check_compatibility_incoming_batch(batch)

        if self.is_batch_already_imported(batch):
            raise IncomingIntegrityError("{}: Batch already imported".format(batch))

        with tarfile.open(IN_PATH + batch) as tar_fd:
            prfx = './' if '.' in tar_fd.getnames() else ''
            self.sql.begin()
            try:
                for file_in_tar in tar_fd.getmembers():
                    set1 = {}
                    if file_in_tar.name.startswith('{}sets/'.format(prfx)) and (file_in_tar.name.count('/') == 1): #TODO: find a way to only extract files in desired subdirectories - this is quite hackish
                        _name = file_in_tar.name.split('{}sets/'.format(prfx))[1]
                        for dyn_file in self.get_dynamic_files():
                            set1[dyn_file] = tar_fd.extractfile("{}sets/{}/{}".format(prfx, _name, dyn_file)).read()
                        self.sql.execute("INSERT INTO `sets` ({} `id`, `batch`) VALUES({} ?, ?)".format(
                                ''.join(('`{}`, '.format(k)) for k in set1),
                                '?, '*len(set1)
                            ),
                            (*set1.values(), _name, batch),
                            commit = False,
                            #tuple(set1.values()) + (_name, batch)
                        )
                self.sql.commit()
            except:
                self.sql.rollback()

    def set_dev_params(self, dev_id, prod_id, fw_ver):
        self.dev_id = dev_id
        self.prod_id = prod_id
        self.fw_ver = fw_ver

    def fetch_set(self, increment):
        # This might need some explanation:
        # `increment` tells whether we want to increment the download_cnt field.
        # We don't want to increment it for every request, but only for a full and completed request.
        # To keep track if a request was initiated (but not yet completed) we increment and negate download_cnt and keep it that way, until download_cnt becomes positive again, which happens when `completed_set` was called.
        # So, a negative download_cnt indicates the last provisioning process wasn't completed.
        # Furthermore, we want - if a set was already assigned to a device (`dev_id`) - to select that one, otherwise a non-assigned, by:
        # ('`dev_id` = ? OR `dev_id` IS NULL ORDER BY `dev_id` DESC LIMIT 1').

        self.sql.execute("UPDATE `sets` SET `dev_id` = ?, `prod_id` = ?, `fw_ver` = ?, `downloaded_cnt` = (CASE WHEN downloaded_cnt>=0 THEN -1*(abs(downloaded_cnt)+1) ELSE downloaded_cnt END), `downloaded_dt` = datetime('now') WHERE `dev_id` = ? OR `dev_id` IS NULL ORDER BY `dev_id` DESC LIMIT 1", (self.dev_id, self.prod_id, self.fw_ver, self.dev_id))
        self.sql.execute("SELECT * FROM `sets` WHERE `dev_id` = ? LIMIT 1", (self.dev_id,))
        set1 = self.sql.fetchone()
        if set1:
            set1 =  dict(zip(set1.keys(), set1))
            set1['purge_code'] = sha256(self.dev_id.encode('utf-8') + self.read_file(LOCAL_PATH_SECRET, binary=True)).hexdigest()
        return set1

    def completed_set(self):
        self.sql.execute("UPDATE `sets` SET `downloaded_cnt` = abs(downloaded_cnt) WHERE `dev_id` = ? LIMIT 1", (self.dev_id,))
        #TODO: confirm changes

    def comment_set(self, id, comment):
        self.sql.execute("UPDATE `sets` SET `comment` = ? WHERE `id` = ?", (comment, id))
        #TODO: confirm changes

    def reset_set(self, id):
        self.sql.execute("UPDATE `sets` SET `dev_id` = NULL WHERE `id` = ?", (id,))
        #TODO: confirm changes

    def assign_set(self, id, dev_id):
        self.sql.execute("UPDATE `sets` SET `dev_id` = ? WHERE `id` = ? AND (`dev_id` IS NULL)", (dev_id, id))
        #TODO: confirm changes


class ProvisioningError(ProvSysError):
    """Derived Exception for high level handling of provisioning errors.
    Supposed to be passed directly to devices to be handled"""

class Provisioning():
    def __init__(self, dev_id, prod_id, fw_ver):
        self.provsys = ProvSystem()
        if not self.provsys.check_consistency_local():
            raise Uninitialized("The provisioning system is not yet initialized")
        self.provsys.parse_config()
        self.cfg = self.provsys.cfg
        self.provsys.set_dev_params(dev_id, prod_id, fw_ver)
        #if dev_id:
        #    self.fetch_set(dev_id, prod_id, fw_ver)

    #def set_dev_params(self, dev_id, prod_id, fw_ver):
    #    self.provsys.set_dev_params(dev_id, prod_id, fw_ver)

    def fetch_set(self, increment = False):
        self.set = self.provsys.fetch_set(increment)
        if not self.set:
            raise NoFree("No unassigned provisioning sets available")

    def fetch_hash(self):
        return sha256(self.provsys.dev_id.encode('utf-8') + self.provsys.read_file(LOCAL_PATH_SECRET, binary=True)).hexdigest()

    def fetch_config(self):
        self.fetch_set(True)
        return self.cfg

    def read_file(self, file_path):
        self.fetch_set()
        return self.provsys.read_file("{}/{}".format(LOCAL_PATH, file_path), binary=True)

    def set_done(self):
        self.fetch_set()
        self.provsys.completed_set()
        return "{}\n{}\n{}".format(self.set['dev_id'], self.set['prod_id'], self.set['fw_ver'])
