FROM python:latest

RUN mkdir /data
WORKDIR /data
COPY . /data

RUN pip install -r requirements.txt

ENTRYPOINT [ "python", "main.py" ]
