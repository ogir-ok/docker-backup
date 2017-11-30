import docker
import re
import os
import time
from datetime import datetime
import shutil
import yaml

BASE_DIR = os.path.dirname(os.path.realpath(__file__))
client = docker.from_env()
settings = {}
try:
    with open(os.path.join(BASE_DIR, 'settings.yml')) as f:
        settings = yaml.load(f)
except Exception as e:
    print(e)

TARGET_DIR = settings.get('TARGET_DIR', os.path.join(BASE_DIR, 'backup'))
STORE_DB_BACKUPS = settings.get('STORE_DB_BACKUPS', 3)
DETACH_RSYNC = settings.get('DETACH_RSYNC', True)


class Backup:
    def __init__(self, container):
        self.container = container
        self.start_date = datetime.now()

    def _env(self, name, default=None):
        env_vars = self.container.attrs['Config']['Env']
        for var in env_vars:
            try:
                var_name, val = var.split('=', 1)
            except ValueError:
                continue
            if var_name == name:
                return val
        return default

    @property
    def _network(self):
        return [*self.container.attrs['NetworkSettings']['Networks'].keys()][0]

    @property
    def target_dir(self):
        return os.path.join(TARGET_DIR, self.container.name)

    @property
    def target_file(self):
        date = self.start_date.strftime('%Y-%m-%dT%H:%M:%S')
        file = '{date}.sql'.format(date=date)
        return os.path.join(self.target_dir, file)

    def perform_backup(self):
        raise NotImplemented

    def clean_old_backups(self):
        pass

    def backup(self):
        start = time.time()
        print("Back up of {} with {}".format(self.container.name, self.__class__.__name__), end='...', flush=True)
        try:
            self.perform_backup()
        except Exception as e:
            print("Failed in {0:.2f}s".format(time.time() - start))
            print(e)
            if os.path.exists(self.target_file):
                if os.path.isdir(self.target_file):
                    shutil.rmtree(self.target_file)
                else:
                    os.unlink(self.target_file)
            return

        print("Done in {0:.2f}s".format(time.time() - start))
        self.clean_old_backups()


class MountsBackup(Backup):
    """
    Backups all volumes of container
    """

    def perform_backup(self):
        for mount in self.container.attrs['Mounts']:
            if mount['Mode'] == 'rw':
                self.backup_mount(mount['Destination'])

    def backup_mount(self, folder):
        target = os.path.join(self.target_dir, folder[1:])
        if target.endswith('/'):
            target = target[:-1]
        if folder.startswith('/'):
            folder += '/'
        client.containers.run('ogirok/docker-backup', 'rsync -avzP {} {}'.format(folder, target), volumes_from=self.container.name, volumes={target: {'bind': target, 'mode': 'rw'}}, remove=True, detach=DETACH_RSYNC)
        if DETACH_RSYNC:
            print('detached', end='...', flush=True)


class DoNotBackup(Backup):
    """
    Does nothing
    """
    def backup(self):
        print("Skipping backup of {}".format(self.container.name))


class DBBackup(Backup):
    cmd = None

    def backup_target(self):
        date = datetime.now().strftime('%Y-%m-%d-%H:%M:%S')
        return '{date}.sql'.format(date=date)

    def clean_old_backups(self):
        for remove in sorted(os.listdir(self.target_dir))[:-STORE_DB_BACKUPS]:
            remove_path = os.path.join(self.target_dir, remove)
            if os.path.isdir(remove_path):
                shutil.rmtree(remove_path)
            else:
                os.unlink(remove_path)
            print('Removed outdated backup', remove)

    def get_environment(self):
        return {}

    def perform_backup(self):

        client.containers.run(self.container.image_name, self.cmd, links={self.container.name: self.container.name},
                              volumes={self.target_dir: {'bind': self.target_dir, 'mode': 'rw'}},
                              environment=self.get_environment(), network=self._network, remove=True)


class PostgresBackup(DBBackup):
    def get_environment(self):
        return {'PGPASSWORD': self._env('POSTGRES_PASSWORD', '')}

    @property
    def cmd(self):
        postgres_user = self._env('POSTGRES_USER', 'postgres')
        return '/bin/bash -c "pg_dumpall -h {host} -U {user} > {backup_file}"' \
            .format(host=self.container.name, user=postgres_user, backup_file=self.target_file)


class MysqlBackup(DBBackup):
    @property
    def cmd(self):
        mysql_root_password = self._env('MYSQL_ROOT_PASSWORD')

        return (
            "/bin/bash -c \""
            " echo '[mysqldump]' > /tmp/my.cnf &&"
            " echo 'host={host}' >> /tmp/my.cnf &&"
            " echo 'user=root' >> /tmp/my.cnf &&"
            " echo 'password=\\\"{password}\\\"' >> /tmp/my.cnf &&"
            " mysqldump --defaults-file=/tmp/my.cnf --all-databases > {backup_file}"
            "\""
        ).format(host=self.container.name, password=mysql_root_password, backup_file=self.target_file)


class MongoBackup(DBBackup):
    @property
    def cmd(self):
        return "/bin/bash -c \"mongodump -h {} --out {}\"".format(self.container.name, self.target_file)


BACKUP_CLASSES = (
    (r'.*postgre.*', PostgresBackup),
    (r'.*mysql.*', MysqlBackup),
    (r'.*mongo.*', MongoBackup),
    (r'.*redis.*', DoNotBackup),
    (r'.*docker-backup.*', DoNotBackup),
    (r'.*', MountsBackup),
)


def backup_container(container):
    for pattern, backup_class in BACKUP_CLASSES:
        for tag in container.image.tags:
            if re.match(pattern, tag):
                container.image_name = tag
                backup_class(container).backup()
                return


if __name__ == '__main__':
    for container in client.containers.list():
        backup_container(container)

