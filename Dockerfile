FROM ubuntu:16.04

ADD ./backup.py /backup.py
ADD ./requirements.txt /requirements.txt
ADD ./crontab /crontab

RUN apt-get update && \
    apt-get install -y rsync docker.io python-pip && \
    pip install devcron && \
    pip install -r /requirements.txt

CMD devcron.py /crontab
