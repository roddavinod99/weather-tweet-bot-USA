# 🤖 Hyderabad Weather Bot

A Python-based Twitter bot that automatically fetches real-time weather data for Hyderabad, India, and posts a detailed, visually-rich weather report to Twitter every few hours. This bot uses the OpenWeatherMap API for weather data and the Tweepy library to interact with the Twitter API.

## ✨ Features

  - **Automated Weather Updates**: Fetches the 5-day, 3-hour forecast for Hyderabad and generates a concise weather summary.
  - **Dynamic Image Generation**: Creates a rich, informative image for each tweet, summarizing the current and upcoming weather conditions.
  - **Intelligent Hashtagging**: Generates relevant hashtags based on current weather conditions (e.g., `#HyderabadRains`, `#Heatwave`, `#Cloudy`).
  - **Tweet Content Optimization**: Automatically adjusts tweet text and hashtags to fit within Twitter's character limit.
  - **Accessible Alt Text**: Generates a detailed alt-text description for the weather image, making the tweet accessible to users with screen readers.
  - **Container-Ready**: Designed to run easily in a containerized environment like Docker or on a platform like Google Cloud Run.
  - **Test Mode**: Includes a safe "Test Mode" to run the application without posting live tweets, allowing for easy testing and debugging.

## ⚙️ How It Works

The bot is built as a lightweight Flask application designed to be triggered by an external scheduler (like a cron job or a Cloud Run scheduler).

1.  **Trigger**: An external scheduler makes a `POST` or `GET` request to the `/run-tweet-task` endpoint.
2.  **Weather Data Fetching**: The application calls the OpenWeatherMap API to get the latest forecast for Hyderabad.
3.  **Content Generation**: It uses the retrieved weather data to:
      - Create the text content for the tweet.
      - Generate a dynamic set of hashtags.
      - Produce a detailed alt-text for accessibility.
      - Create an image using the `Pillow` library, which contains a formatted summary of the weather.
4.  **Twitter API Interaction**: The bot uses both Twitter API v1.1 and v2 clients via the `tweepy` library. The v1.1 client handles the image upload, while the v2 client is used for posting the final tweet.
5.  **Post**: The tweet, complete with the generated image and alt text, is posted to the configured Twitter account.

## 🚀 Getting Started

To get this bot up and running, you'll need to set up API keys and configure a few environment variables.

### Prerequisites

  - A Twitter Developer account and a Project/App with **Elevated Access** or higher.
  - An OpenWeatherMap API key.
  - Python 3.8+ installed.

### Step 1: Clone the Repository

```bash
git clone https://github.com/your-username/your-repo.git
cd your-repo
```

### Step 2: Install Dependencies

This project relies on a few Python libraries. It's best practice to use a virtual environment.

```bash
# Create a virtual environment
python -m venv venv

# Activate the virtual environment
# On macOS/Linux:
source venv/bin/activate
# On Windows:
venv\Scripts\activate

# Install the required libraries
pip install -r requirements.txt
```

You'll need to create a `requirements.txt` file in your project root with the following content:

```
Flask
tweepy
requests
pytz
Pillow
```

### Step 3: Configure Environment Variables

The application relies on environment variables for sensitive API keys and configuration settings. You can set these directly in your deployment environment or use a `.env` file for local development.

  - **Twitter API Keys**:
      - `TWITTER_API_KEY`
      - `TWITTER_API_SECRET`
      - `TWITTER_ACCESS_TOKEN`
      - `TWITTER_ACCESS_TOKEN_SECRET`
  - **Weather API Key**:
      - `WEATHER_API_KEY`
  - **Configuration**:
      - `POST_TO_TWITTER_ENABLED`: Set to `true` to enable live tweeting. Set to `false` for test mode (default).
      - `PORT`: The port on which the Flask application will run (e.g., `8080`).

**Example `.env` file for local testing:**

```
TWITTER_API_KEY="your_consumer_key_here"
TWITTER_API_SECRET="your_consumer_secret_here"
TWITTER_ACCESS_TOKEN="your_access_token_here"
TWITTER_ACCESS_TOKEN_SECRET="your_access_token_secret_here"

WEATHER_API_KEY="your_openweathermap_api_key_here"

POST_TO_TWITTER_ENABLED="false"
```

### Step 4: Run the Application

You can run the application locally to test it.

```bash
python your_main_script_name.py
```

Replace `your_main_script_name.py` with the actual filename (e.g., `app.py` or `bot.py`).

The server will start, and you can now trigger the tweet task manually by visiting `http://localhost:8080/run-tweet-task` in your browser or by sending a `GET` or `POST` request.

### Step 5: Setting Up a Scheduler

For this bot to run automatically, you need a scheduler. This is a crucial step for deployment.

  - **Cron Job (Linux/macOS)**: You can use a cron job to call the endpoint at a specific time.
    ```crontab
    # Every 3 hours
    0 */3 * * * curl http://your-deployed-url/run-tweet-task
    ```
  - **Cloud Run Scheduler (Google Cloud)**: If you're using Google Cloud, you can use a Cloud Scheduler job to make a request to your deployed Cloud Run service.

## 🤝 Contributing

Contributions are welcome\! If you find a bug or have a suggestion, please open an issue or submit a pull request.
