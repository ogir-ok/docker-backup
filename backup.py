import docker
import re

client = docker.from_env()

print client.containers()[0]

for container in client.containers():
    print container.get('Image'), container.get('Names')[0]
    print container.get('Mounts')

class Backup(object):
    def __init__(self, container):
        self.container = container

    def backup(self):
        raise NotImplemented

class MountsBackup(self):
    def backup(self):
        client.containers.run()

class DoNotBackup(Backup):
    def backup(self):
        return


def backup_container(container):
    pass

