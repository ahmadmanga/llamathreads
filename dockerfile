# Use an official Python image as the base
FROM python:3.10.12

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file
COPY requirements.txt .

# Install the dependencies
RUN pip install -r requirements.txt

# Copy the application code
COPY . .

# Make the command to run the application
CMD ["python", "main.py"]
