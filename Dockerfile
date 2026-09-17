# The only Dockerfile in this repository. Judges run, from the repo root:
#   docker build -t team .
#   docker run --rm -e FEATHERLESS_API_KEY -v <bundle>:/data:ro -v <empty>:/out team \
#       python run.py --dataset /data --queries /data/query.csv --out /out
FROM python:3.12-slim
WORKDIR /app
COPY track-1/starter/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY track-1/starter/ .
