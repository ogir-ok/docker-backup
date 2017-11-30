FROM ubuntu:16.04

ADD . /src

RUN apt-get update && \
    apt-get install -y rsync docker.io python-pip && \
    pip install devcron && \
    pip install -r /src/requirements.txt


CMD devcron.py /src/crontab
