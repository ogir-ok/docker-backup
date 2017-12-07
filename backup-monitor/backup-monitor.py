import asyncio
import os
import time
from contextlib import contextmanager
from datetime import datetime
from dateutil import parser
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.sql.elements import not_

Base = declarative_base()
BACKUP_DIR = os.getenv('BACKUP_SOURCE', '/backup')

HOST = os.getenv('HOST', 'local')
MYSQL_HOST = os.getenv('MYSQL_HOST', 'mysql')
MYSQL_USER = os.getenv('MYSQL_USER', 'root')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD', 'test37')


engine = create_engine('mysql+pymysql://{}:{}@{}/backups'.format(MYSQL_USER, MYSQL_PASSWORD, MYSQL_HOST))
Session = sessionmaker(bind=engine)


@contextmanager
def session_scope():
    """Provide a transactional scope around a series of operations."""
    session = Session()
    try:
        yield session
        session.commit()
    except:
        session.rollback()
        raise
    finally:
        session.close()



def get_fs_size(path):
    size = 0
    if os.path.isdir(path):
        for (path, dirs, files) in os.walk(path):
            for file in files:
                filename = os.path.join(path, file)
                size += os.path.getsize(filename)
    else:
        size = os.path.getsize(path)
    return size


class Backup(Base):
    __tablename__ = 'backups'

    id = Column(Integer, primary_key=True)
    host = Column(String(255))
    container = Column(String(255))
    size = Column(Integer)
    date = Column(DateTime)


class BackupCollection:
    def __init__(self, container):
        self.container = container
        self.backups = []
        self.path = os.path.join(BACKUP_DIR, container)

    def parse(self):
        files = os.listdir(self.path)
        if all(p.endswith('.sql') for p in files):
            #this is db
            self.backups = [BackupEntry(self.container, os.path.join(self.path, backup)) for backup in files]
        else:
            self.backups = [BackupEntry(self.container, self.path)]


class BackupEntry:
    def __init__(self, container, path):
        self.path = path
        self.dir, self.name = os.path.split(self.path)
        self.date = self.get_date()
        self.container = container
        self.size = -1

        with session_scope() as session:
            self.instance = session.query(Backup).filter_by(date=self.date, container=self.container).first()


    def get_date(self):
        name, ext = os.path.splitext(self.name)
        try:
            return parser.parse(name)
        except Exception:
            return datetime.fromtimestamp(os.path.getctime(self.path)).date()

    async def save(self):
        self.size = get_fs_size(self.path)
        if not self.instance:
            self.instance = Backup(host=HOST, container=self.container, size=self.size, date=self.date)
        with session_scope() as session:
            session.add(self.instance)
            session.commit()
            print(self)
            return self.instance.id

    def __repr__(self):
        return '<BU: {} {}>'.format(self.container, self.date)


def gather_backups(backup_dir=BACKUP_DIR):
    print('Starting gathering backups')
    backup_collections = []
    for container in os.listdir(backup_dir):
        backups_path = os.path.join(backup_dir, container)
        if os.path.isdir(backups_path):
            collection = BackupCollection(container)
            collection.parse()
            backup_collections.append(collection)

    request = []
    for collection in backup_collections:
        for backup in collection.backups:
            request.append(backup.save())

    loop = asyncio.get_event_loop()
    host_ids = loop.run_until_complete(asyncio.gather(*request))
    loop.close()

    with session_scope() as session:
        outdated = session.query(Backup).filter_by(host=HOST).filter(not_(Backup.id.in_(host_ids)))
        print('Outdated', outdated.count())
        outdated.delete(synchronize_session='fetch')
        session.commit()

def main():

    Base.metadata.create_all(engine)

    while True:
        try:
            gather_backups()
        except Exception as e:
            raise

        time.sleep(3600 * 12)


if __name__ == '__main__':
    main()
