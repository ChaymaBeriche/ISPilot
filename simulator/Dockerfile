FROM python:3.11-slim
WORKDIR /app
RUN pip install requests
COPY tr069_sim.py .
CMD ["python", "-u", "tr069_sim.py"]