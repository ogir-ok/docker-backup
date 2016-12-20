#!python
import re
import os
import sys
import time
import subprocess
import shutil
from datetime import datetime
from docker import Client

DOCKER_URL = 'unix://var/run/docker.sock'
BACKUP_DIR = '/mnt/backup-server/'
BACKUP_IMAGE = 'docker-backup'
STORE_DB_BACKUPS = 3

cli = Client(base_url=DOCKER_URL)
containers = cli.containers()

SKIP_REGEX = [re.compile(r".*docker-backup.*"), re.compile(r'.*redis.*') ]

class Backup(object):
    REGEX = None

    def __init__(self, container):
        self.container = container
        self.backup_image = container['Image']

    def _env(self, name):
        env_vars = cli.inspect_container(self.container['Id'])['Config']['Env']
        for var in env_vars:
            try:
                var_name, val = var.split('=')
            except ValueError:
                continue
            if var_name == name:
                return val


    def backup_dir(self):
        backup_dir = os.path.join(BACKUP_DIR, self.container['Names'][0].replace('/', ''))
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)
        return backup_dir

    def backup_target(self):
        return self.backup_dir()

    def do_backup(self):
        cmd = ['docker', 'run', '--rm', '-it',
               '--link', '{}:source'.format(self.container['Id']),
               '--volumes-from', self.container['Id'],
               '-v', '{}:/backup/'.format(self.backup_dir()),
               self.backup_image,
               "/bin/bash", "-c",
               self.backup_command()
               ]
        os.system(' '.join(cmd))

    def clean_backup(self):
        backup_dir = self.backup_dir()
        for remove in sorted(os.listdir(backup_dir)[:-STORE_DB_BACKUPS]):
            remove_path = os.path.join(backup_dir, remove)
            if os.path.isdir(remove_path):
                shutil.rmtree(remove_path)
            else:
                os.unlink(remove_path)

    def backup(self):
        start = time.time()
        print("Back up of {} with {}".format(self.container['Names'][0], self.__class__.__name__))
        self.do_backup()
        self.clean_backup()
        print("Done in {}".format(time.time() - start))


class DBBackup(Backup):

    def backup_target(self):
        date = datetime.now().strftime('%Y-%m-%d-%H:%M:%S')
        return '{date}.sql'.format(date=date)


class MySQLBackup(DBBackup):
    REGEX = re.compile(r'.*(mysql|mariadb).*')

    def backup_command(self):
        mysql_root_password = self._env('MYSQL_ROOT_PASSWORD')
        backup_file = self.backup_target()
        return ("\""
                "echo '[mysqldump]' > /tmp/my.cnf &&"
                " echo 'host=source' >> /tmp/my.cnf &&"
                " echo 'user=root' >> /tmp/my.cnf &&"
                " echo 'password=\\\"{password}\\\"' >> /tmp/my.cnf &&"
                " mysqldump --defaults-file=/tmp/my.cnf --all-databases > {backup_file}"
                "\"").format(password=mysql_root_password, backup_file=os.path.join('/backup/', backup_file))


class PGBackup(DBBackup):
    REGEX = re.compile(r'.*postgres.*')

    def backup_command(self):
        postgres_user = self._env('POSTGRES_USER')
        if not postgres_user:
            postgres_user = 'postgres'

        postgres_password = self._env('POSTGRES_PASSWORD')
        backup_file = self.backup_target()
        return ("\"env PGPASSWORD={password} pg_dumpall -h source -U {user} > {backup_file}\"")\
            .format(password=postgres_password, user=postgres_user, backup_file=os.path.join('/backup/', backup_file))


class MongoBackup(DBBackup):
    REGEX = re.compile(r'.*mongo.*')

    def backup_target(self):
        bt = super(MongoBackup, self).backup_target()
        os.makedirs(os.path.join(self.backup_dir(), bt))
        return bt

    def backup_command(self):
        backup_file = self.backup_target()
        return ("\"mongodump -h source --out {}\"".format(os.path.join('/backup/', backup_file)))


class VolumesBackup(Backup):
    def __init__(self, container):
        super(VolumesBackup, self).__init__(container)
        self.backup_image = BACKUP_IMAGE

    def clean_backup(self):
        pass

    def backup_command(self):
        volumes = cli.inspect_container(self.container['Id'])['Mounts']

        rsyncs = []

        for volume in volumes:
            volume_path = volume['Destination']
            volume_dest = os.path.join('/backup/', os.path.basename(volume_path))

            rsyncs.append("rsync -rtP {} {} > /dev/null".format(volume_path, volume_dest))
        return '"{}"'.format(' && '.join(rsyncs))


BACKUP_CLASSES = [MongoBackup, PGBackup, MySQLBackup]
DEFAULT_BACKUP_CLASS = VolumesBackup
BACKUP_TASKS = []


def main():
    print("Starting back up at {}".format(datetime.now()))
    for container in containers:
        skip = False
        do_default = True
        for skip_re in SKIP_REGEX:
            if skip_re.match(container['Image']):
                skip = True

        if skip:
            continue

        for cls in BACKUP_CLASSES:
            if (cls.REGEX.match(container['Image'])):
                BACKUP_TASKS.append(cls(container))
                do_default = False

        if do_default:
            BACKUP_TASKS.append(DEFAULT_BACKUP_CLASS(container))

    for task in BACKUP_TASKS:
        task.backup()


if __name__ == '__main__':
    main()


