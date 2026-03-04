FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/chuckbot

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app.py toolforge_queue_api.py process_queue.py buckbot_rollback_worker.py policy_compliance_check.py ./
COPY toolforge_container_entrypoint.sh ./
RUN chmod +x /srv/chuckbot/toolforge_container_entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/srv/chuckbot/toolforge_container_entrypoint.sh"]
CMD ["web"]
