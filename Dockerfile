FROM python:3.12-slim
WORKDIR /app
COPY src/ ./src/
EXPOSE 25293
CMD ["python", "-u", "src/daemon.py"]
