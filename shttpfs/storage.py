import os.path as p
import os, shutil, json, errno, fcntl
from collections import deque

from common import cpjoin

############################################################################################
# Journaling file storage subsystem, only use one instance at any time, not thread safe
############################################################################################
class storage(object):

############################################################################################
    def __init__(self, data_dir, conf_dir):
        # need to make sure data dir path has a trailing slash
        self.data_dir    = data_dir
        self.j_file      = self.mkfs_path(conf_dir, 'journal.json')
        self.tmp_dir     = self.mkfs_path(conf_dir, 'tmp')
        self.backup_dir  = self.mkfs_path(conf_dir, 'back')
        self.journal     = None
        self.lock        = None
        self.tmp_idx     = 0

    # Make sure tmp dir exists
        try: os.makedirs(self.tmp_dir)
        except: pass

    # Make sure backup dir exists
        try: os.makedirs(self.backup_dir)
        except: pass

############################################################################################
    def lock(self, l_type):
        """
        Lock the file system, returns true on success, false on failure
        
        lock types:
        'shared'    - shared lock
        'exclusive' - exclusive lock
        """

        if self.lock != None:
            return True

        try:
            l_type = {'shared' : pfcntl.LOCK_SH, 'exclusive' : pfcntl.LOCK_EX}[l_type]
            fd = open(self.data_dir + 'lock_file', 'w')
            fcntl.flock(fd, l_type | fcntl.LOCK_NB)
            self.lock = fd # assigned last so that does not indicate a lock exists if locking failed
            return True

        except IOError:
            return False

############################################################################################
    def unlock():
        """ Unlock the file system """

        lock = self.lock
        self.lock = None
        fcntl.flock(lock, l_type | fcntl.LOCK_NB)

############################################################################################
    def mkfs_path(self, *args):
        """ make path relative to DATA DIR from a system relative path """

        return cpjoin(self.data_dir, *args)

############################################################################################
    def new_tmp(self):
        """ Create a new temp file allocation """

        self.tmp_idx += 1
        return p.join(self.tmp_dir, 'tmp_' + str(self.tmp_idx)) 

############################################################################################
    def new_backup(self, src):
        """ Create a new backup file allocation """

        backup_id_file = p.join(self.backup_dir, '.bk_idx')
        try: backup_num = int(self.file_get_contents(backup_id_file))
        except: backup_num = 1

        backup_name = str(backup_num) + "_" + os.path.basename(src)
        backup_num += 1

        try: os.makedirs(bk_path)
        except: pass

        with open(backup_id_file, 'w') as f: 
            f.write(str(backup_num))
        return p.join(self.backup_dir, backup_name)

############################################################################################
    def begin(self):
        """ Begin a transaction """

        if self.journal != None:
            raise Exception('Storage is already active, nested begin not supported')

        # under normal operation journal is deleted at end of transaction
        # if it does exist we need to roll back
        if os.path.isfile(self.j_file):  
            self.rollback()

        self.journal = open(self.j_file, 'w')

############################################################################################
    def do_action(self, command, journal = True):
        """ Implementation for declarative file operations. """

        cmd = 0; src = 1; path = 1; data = 2; dst = 2

        if journal == True:
            self.journal.write(json.dumps(command['undo']) + "\n")
            self.journal.flush()

        d = command['do']
        if d[cmd] == 'copy':
            shutil.copy(d[src], d[dst])

        elif d[cmd] == 'move':
            shutil.move(d[src], d[dst])

        elif d[cmd] == 'backup':
            shutil.move(d[src], self.new_backup(d[src]))

        elif d[cmd] == 'write' :
            if callable(d[data]):
                d[data](d[path])
            else:
                with open(d[path], 'w') as f:
                    f.write(d[data])

############################################################################################
    def rollback(self):
        """ Do journal rollback """

        # Close the journal for writing, if this is an automatic rollback following a crash,
        # the file descriptor will not be open, so don't need to do anything.
        if self.journal != None:
            self.journal.close()
        self.journal = None

        # Read the journal
        journal = self.file_get_contents(self.j_file)

        journ_list = []
        with open(self.j_file) as fle:
            for l in fle: journ_list.append(json.loads(l))

        journ_subtract = deque(reversed(journ_list))

        for j_itm in reversed(journ_list):
            print j_itm

            try:
                self.do_action({'do' : j_itm}, False)
            except IOError: pass

            # As each item is completed remove it from the journal file, in case
            # something fails during the rollback we can pick up where it stopped.
            journ_subtract.popleft()
            with open(self.j_file, 'w') as f: 
                for data in list(journ_subtract):
                    f.write(json.dumps(data) + "\n")
                f.flush()
            
        # Rollback is complete so delete the journal file
        os.remove(self.j_file)

############################################################################################
    def commit(self, cont = False):
        """ Finish a transaction """

        self.journal.close()
        self.journal = None
        os.remove(self.j_file)

        if(cont == True):
            self.begin()
            
############################################################################################
    def file_get_contents(self, path):
        """ Returns contents of file located at 'path', not changing FS so does
        not require journaling """

        with open(path, 'r') as f:
            return  f.read()

############################################################################################
    def file_put_contents(self, path, data):
        """ Put passed contents into file located at 'path' """

        # if file exists, create a temp copy to allow rollback
        if os.path.isfile(path):  
            tmp_path = self.new_tmp()
            self.do_action({
                'do'   : ['copy', path, tmp_path],
                'undo' : ['move', tmp_path, path]})

        self.do_action(
            {'do'   : ['write', path, data],
             'undo' : ['backup', path]})

############################################################################################
    def move_file(self, src, dst):
        """ Move file from src to dst """

        # record where file moved
        if os.path.isfile(src):  
            # if destination file exists, copy it to tmp first
            if os.path.isfile(dst):  
                tmp_path = self.new_tmp()
                self.do_action({
                    'do'   : ['copy', dst, tmp_path],
                    'undo' : ['move', tmp_path, dst]})

        self.do_action(
            {'do'   : ['move', src, dst],
             'undo' : ['move', dst, src]})

############################################################################################
    def delete_file(self, path):
        """ delete a file """

        # if file exists, create a temp copy to allow rollback
        if os.path.isfile(path):  
            tmp_path = self.new_tmp()
            self.do_action({
                'do'   : ['copy', path, tmp_path],
                'undo' : ['move', tmp_path, path]})

        else:
            raise OSError(errno.ENOENT, 'No such file or directory', path)

