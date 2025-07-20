# Dockerfile
# Use the official Python image as a base
FROM python:3.9-slim-buster

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install the Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
COPY . .

# Ensure the image file exists
# If 'It's going to Rain.png' is dynamic or very large, consider Cloud Storage.
# For now, bundling it is fine.
COPY its_going_to_rain.png .

# Expose the port your Flask app will run on. Cloud Run expects 8080 by default.
# The `PORT` environment variable will be set by Cloud Run.
ENV PORT 8080

# Command to run the application using Gunicorn
# Gunicorn will serve the Flask app 'app' (from app.py)
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app