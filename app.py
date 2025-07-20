# app.py

import os
import requests
from flask import Flask, jsonify, request
import logging
from datetime import datetime, timedelta
import pytz
import json
import tweepy

# --- Configuration ---
# Set up basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# API Keys from environment variables
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY")
TWITTER_API_KEY = os.environ.get("TWITTER_API_KEY")
TWITTER_API_SECRET = os.environ.get("TWITTER_API_SECRET")
TWITTER_ACCESS_TOKEN = os.environ.get("TWITTER_ACCESS_TOKEN")
TWITTER_ACCESS_TOKEN_SECRET = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET")

# Flag to enable/disable actual tweeting (for testing)
# Set POST_TO_TWITTER_ENABLED to "true" in Cloud Run environment variables to enable tweeting
POST_TO_TWITTER_ENABLED = os.environ.get("POST_TO_TWITTER_ENABLED", "false").lower() == "true"

# Define cities and their coordinates (Latitude, Longitude)
# IMPORTANT: This list is intended to be the 10 US cities for your project.
CITIES = {
    "New York City": (40.7128, -74.0060),
    "Los Angeles": (34.0522, -118.2437),
    "Chicago": (41.8781, -87.6298),
    "Houston": (29.7604, -95.3698),
    "Phoenix": (33.4484, -112.0740),
    "Philadelphia": (39.9526, -75.1652),
    "San Antonio": (29.4241, -98.4936),
    "San Diego": (32.7157, -117.1611),
    "Dallas": (32.7767, -96.7970),
    "San Jose": (37.3382, -121.8863)
}

# Timezone for the weather updates (Eastern Time for US cities)
BOT_TIMEZONE = pytz.timezone('America/New_York')

# Log file for cycling cities and last clear time
LOG_FILE_PATH = "city_tweet_log.json"

# Image path - ensure this matches the renamed file
IMAGE_PATH_RAIN = "its_going_to_rain.png" # This image will now always be attached

# Interval to clear the log and reset city cycle (in hours)
# If set to 10, the log will clear every 10 hours from the last clear time.
LOG_CLEAR_INTERVAL_HOURS = 10

app = Flask(__name__)

# --- Helper Functions ---

def read_log_file():
    """Reads the last posted city and last clear time from the log file."""
    if not os.path.exists(LOG_FILE_PATH):
        return {"last_posted_city": None, "last_clear_time_utc": None}
    try:
        with open(LOG_FILE_PATH, 'r') as f:
            data = json.load(f)
            return data
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON from log file: {e}. Starting with empty log.")
        return {"last_posted_city": None, "last_clear_time_utc": None}
    except Exception as e:
        logging.error(f"Error reading log file: {e}. Starting with empty log.")
        return {"last_posted_city": None, "last_clear_time_utc": None}

def write_log_file(last_posted_city, last_clear_time_utc):
    """Writes the last posted city and last clear time to the log file."""
    data = {"last_posted_city": last_posted_city, "last_clear_time_utc": last_clear_time_utc}
    try:
        with open(LOG_FILE_PATH, 'w') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logging.error(f"Error writing to log file: {e}")

def get_next_city(last_city):
    """Determines the next city to tweet about in a cycle."""
    city_names = list(CITIES.keys()) # Use the CITIES dictionary for the list of city names
    if last_city is None or last_city not in city_names:
        return city_names[0]  # Start with the first city (e.g., New York City)
    else:
        current_index = city_names.index(last_city)
        next_index = (current_index + 1) % len(city_names)
        return city_names[next_index]

def deg_to_compass(deg):
    """Converts degrees to cardinal compass direction."""
    dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    ix = int((deg + 11.25) / 22.5)
    return dirs[ix % 16]

def get_weather_forecast(city_name):
    """
    Fetches 5-day/3-hour weather forecast data using OpenWeatherMap's 'forecast' endpoint.
    """
    weather_api_key = os.environ.get("WEATHER_API_KEY")
    if not weather_api_key:
        logging.error("WEATHER_API_KEY not found. Cannot fetch weather.")
        return None

    # Use the 'forecast' endpoint, which your working code uses, with city name
    url = f'https://api.openweathermap.org/data/2.5/forecast?q={city_name}&appid={weather_api_key}&units=metric'
    
    logging.info(f"Fetching weather forecast from: {url.replace(weather_api_key, 'YOUR_API_KEY_DISPLAY')}")

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        return response.json()
    except requests.exceptions.RequestException as err:
        logging.error(f"Error fetching weather forecast data for {city_name}: {err}")
        logging.error(f"Failed URL (check API key/city name): {url.replace(weather_api_key, 'YOUR_API_KEY_DISPLAY')}")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred while fetching weather for {city_name}: {e}")
        return None

def create_weather_tweet_content(city, forecast_data):
    """
    Creates the main tweet content with emojis and conditional rain message.
    Extracts data from the 'forecast' API response structure.
    """
    if not forecast_data or 'list' not in forecast_data or not forecast_data['list']:
        logging.error("No valid forecast data provided to create_weather_tweet_content.")
        return {"lines": ["Could not generate weather report: Data missing."], "current_weather": {}, "hourly_forecast": []}

    # Use the BOT_TIMEZONE for current time
    current_time_local = datetime.now(BOT_TIMEZONE)
    day_of_week = current_time_local.strftime('%A')
    current_time_str = current_time_local.strftime('%I:%M %p') # e.g., 10:00 AM

    # --- Extract Current Weather Details (from the first item in the forecast list) ---
    current_weather_data = forecast_data['list'][0]
    main_conditions = current_weather_data.get('main', {})
    wind_conditions = current_weather_data.get('wind', {})
    weather_info = current_weather_data.get('weather', [{}])[0]

    sky_description = weather_info.get('description', "N/A").capitalize()
    temp_celsius = round(main_conditions.get('temp', 0))
    feels_like_celsius = round(main_conditions.get('feels_like', 0))
    humidity = main_conditions.get('humidity', 0)
    wind_speed_kph = round(wind_conditions.get('speed', 0) * 3.6) # Convert m/s to km/h
    wind_direction = deg_to_compass(wind_conditions.get('deg', 0))

    # --- Determine Chance of Rain for Tweet Message ---
    chance_of_rain_percentage = 0
    # Look at the next 4 intervals (12 hours) of the 3-hour forecast
    for i in range(1, min(5, len(forecast_data['list']))):
        forecast_item = forecast_data['list'][i]
        pop = forecast_item.get('pop', 0) # Probability of Precipitation (0-1)
        rain_3h = forecast_item.get('rain', {}).get('3h', 0) # Rain volume for 3 hours

        chance_of_rain_percentage = max(chance_of_rain_percentage, int(pop * 100))
        if rain_3h > 0:
            chance_of_rain_percentage = max(chance_of_rain_percentage, 25) # Minimum 25% if any rain volume

    rain_message = ""
    if chance_of_rain_percentage >= 20: # Threshold for "rain expected"
        rain_message = f"☔ Chance of rain: {chance_of_rain_percentage}%"
    else:
        rain_message = "☔ No significant rain expected soon."

    # --- Main Tweet Content Assembly ---
    tweet_text_lines = [
        f"Hello, {city}!👋, {day_of_week} weather as of {current_time_str}:",
        f"☁️ Sky: {sky_description}",
        f"🌡️ Temp: {temp_celsius}°C (feels: {feels_like_celsius}°C)",
        f"💧 Humidity: {humidity}%",
        f"💨 Wind: {wind_speed_kph} km/h from the {wind_direction}",
        rain_message, # Conditional rain message
        "Have a great day! 😊"
    ]

    return {
        "lines": tweet_text_lines,
        "current_weather": { # Pass current weather details for ALT text
            'temp': temp_celsius, 'feels_like': feels_like_celsius, 'humidity': humidity,
            'pressure': main_conditions.get('pressure', 0), 'wind_speed': wind_speed_kph,
            'wind_deg': wind_conditions.get('deg', 0), 'sky': sky_description,
            'visibility': current_weather_data.get('visibility', 10000) / 1000, # km
            'clouds': current_weather_data.get('clouds', {}).get('all', 0)
        },
        "hourly_forecast": forecast_data['list'][1:5] # Next 4 intervals (12 hours) for ALT text
    }

def create_alt_text_from_forecast(city_name, current_weather_details, hourly_forecast_data):
    """
    Generates detailed alt text for the image based on the new format,
    using data extracted from the 'forecast' API response structure.
    """
    current_time_local = datetime.now(BOT_TIMEZONE)

    # Current weather part
    alt_text = f"Current weather in {city_name} at {current_time_local.strftime('%I:%M %p')}:\n"
    alt_text += (
        f"It's about {current_weather_details['temp']}°C, but feels like {current_weather_details['feels_like']}°C "
        f"with {current_weather_details['sky'].lower()} skies. Humidity is {current_weather_details['humidity']}%, "
        f"pressure {current_weather_details['pressure']} hPa. Wind is {current_weather_details['wind_speed']} km/h "
        f"from the {deg_to_compass(current_weather_details['wind_deg'])}. Visibility around {current_weather_details['visibility']:.0f} km, "
        f"and cloudiness is {current_weather_details['clouds']}%. \n\n"
    )

    alt_text += "-------------------><-----------------------\n\n" # Separator
    alt_text += "Here's what to expect for the next 12 hours:\n"
    
    # Hourly forecast part - using the next 4 intervals (12 hours total, 3-hour steps)
    if hourly_forecast_data:
        for forecast_item in hourly_forecast_data:
            forecast_time_utc = datetime.fromtimestamp(forecast_item['dt'], tz=pytz.utc)
            forecast_time_local = forecast_time_utc.astimezone(BOT_TIMEZONE)
            
            temp = round(forecast_item.get('main', {}).get('temp', 0))
            description = forecast_item.get('weather', [{}])[0].get('description', 'N/A').capitalize()
            pop = int(forecast_item.get('pop', 0) * 100) # Probability of Precipitation
            rain_volume = forecast_item.get('rain', {}).get('3h', 0) # Rain volume for 3 hours

            rain_info = ""
            if pop > 0:
                rain_info = f"Chance of rain: {pop}%."
                if rain_volume > 0:
                    rain_info += f" ({rain_volume:.1f}mm expected)."
            
            alt_text += (
                f"By {forecast_time_local.strftime('%I %p')}: Expect {description} around {temp}°C. {rain_info}\n"
            )
    else:
        alt_text += "Hourly forecast data is not available."
    
    # Twitter alt text limit is 1000 characters
    if len(alt_text) > 1000:
        logging.warning(f"Alt text exceeded 1000 characters ({len(alt_text)}). Truncating.")
        alt_text = alt_text[:997] + "..."

    return alt_text

def generate_dynamic_hashtags(current_city, forecast_data):
    """
    Generates dynamic hashtags based on the city, region, and weather conditions.
    """
    hashtags = set() # Use a set to avoid duplicates

    city_hashtag = "#" + "".join(word for word in current_city.split() if word.isalnum())
    hashtags.add(city_hashtag)
    hashtags.add("#weatherupdate")

    # Add a general US weather hashtag, as your CITIES list is currently US-based
    hashtags.add("#USWeather")
    
    # Determine if it's the weekend for #WeekendWeather
    current_time_local = datetime.now(BOT_TIMEZONE)
    is_weekend = current_time_local.weekday() in [4, 5, 6] # Friday (4), Saturday (5), Sunday (6)
    if is_weekend:
        hashtags.add("#WeekendWeather")

    # Check for rain forecast in the next 12 hours (4 intervals)
    rain_imminent = False
    if forecast_data and 'list' in forecast_data:
        for i in range(1, min(5, len(forecast_data['list']))): # Check next 4 intervals
            forecast_item = forecast_data['list'][i]
            weather_id = forecast_item.get('weather', [{}])[0].get('id', 800)
            pop = forecast_item.get('pop', 0)
            rain_3h = forecast_item.get('rain', {}).get('3h', 0)

            if (200 <= weather_id < 600) or (pop > 0.2) or (rain_3h > 0): # Rain (ID 2xx-5xx), high POP, or actual rain volume
                rain_imminent = True
                break
    
    if rain_imminent:
        hashtags.add("#RainyWeather") # New hashtag for rain

    return list(hashtags)

# --- Initialize Twitter API Clients (v1.1 for media, v2 for tweets) ---
bot_api_client_v2 = None
bot_api_client_v1 = None
try:
    consumer_key = os.environ.get("TWITTER_API_KEY")
    consumer_secret = os.environ.get("TWITTER_API_SECRET")
    access_token = os.environ.get("TWITTER_ACCESS_TOKEN")
    access_token_secret = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET")

    # Check if all keys are present before initializing
    if not all([consumer_key, consumer_secret, access_token, access_token_secret]):
        raise EnvironmentError("One or more Twitter API environment variables are missing.")

    # v2 client for creating tweets
    bot_api_client_v2 = tweepy.Client(
        consumer_key=consumer_key, consumer_secret=consumer_secret,
        access_token=access_token, access_token_secret=access_token_secret
    )

    # v1.1 client is needed for media uploads and metadata
    auth = tweepy.OAuth1UserHandler(consumer_key, consumer_secret, access_token, access_token_secret)
    bot_api_client_v1 = tweepy.API(auth)

    logging.info("Twitter v1.1 and v2 clients initialized successfully.")
except EnvironmentError as e:
    logging.error(f"Error initializing Twitter clients due to missing environment variable: {e}")
    # Set POST_TO_TWITTER_ENABLED to False if keys are missing
    POST_TO_TWITTER_ENABLED = False 
except Exception as e:
    logging.critical(f"An unexpected error occurred during Twitter client initialization: {e}")
    POST_TO_TWITTER_ENABLED = False


# --- Tweeting Function ---
def tweet_post(tweet_components, current_city):
    """Assembles and posts a tweet with an image and alt text."""
    if not POST_TO_TWITTER_ENABLED:
        logging.info("[TEST MODE] Skipping actual tweet post.")
        logging.info("--- Simulated Tweet ---")
        logging.info("\n".join(tweet_components['lines']))
        logging.info(f"Hashtags: {' '.join(tweet_components['hashtags'])}")
        logging.info(f"Image: '{IMAGE_PATH_RAIN}' with Alt Text:\n{tweet_components['alt_text']}")
        logging.info("--- End Simulated Tweet ---")
        return True

    if not all([bot_api_client_v1, bot_api_client_v2]):
        logging.error("Twitter clients not initialized. Aborting tweet post.")
        return False
        
    body = "\n".join(tweet_components['lines'])
    hashtags = tweet_components['hashtags']

    full_tweet = f"{body}\n{' '.join(hashtags)}"
    
    # Character limit check and adjustment
    TWITTER_MAX_CHARS = 280
    if len(full_tweet) > TWITTER_MAX_CHARS:
        logging.warning(f"Tweet content ({len(full_tweet)} chars) exceeds {TWITTER_MAX_CHARS} char limit. Adjusting hashtags.")
        # Attempt to remove hashtags until it fits
        while hashtags and len(f"{body}\n{' '.join(hashtags)}") > TWITTER_MAX_CHARS:
            hashtags.pop()
        tweet_text = f"{body}\n{' '.join(hashtags)}" if hashtags else body
        logging.warning(f"Adjusted tweet length: {len(tweet_text)} chars.")
    else:
        tweet_text = full_tweet

    media_ids = []
    if not os.path.exists(IMAGE_PATH_RAIN):
        logging.error(f"Image not found at '{IMAGE_PATH_RAIN}'. Posting tweet without image.")
    else:
        try:
            logging.info(f"Uploading media: {IMAGE_PATH_RAIN} for {current_city}")
            media = bot_api_client_v1.media_upload(filename=IMAGE_PATH_RAIN)
            media_ids.append(media.media_id)
            bot_api_client_v1.create_media_metadata(media_id=media.media_id, alt_text=tweet_components['alt_text'])
            logging.info("Media uploaded and alt text added successfully.")
        except Exception as e:
            logging.error(f"Failed to upload media or add alt text for {current_city}: {e}")
            # Do not return False here, try to post text-only tweet if image failed
            media_ids = [] # Clear media_ids if upload failed

    try:
        # Pass media_ids as None if the list is empty
        bot_api_client_v2.create_tweet(text=tweet_text, media_ids=media_ids if media_ids else None)
        logging.info(f"Tweet for {current_city} posted successfully to Twitter!")
        logging.info(f"Final Tweet ({len(tweet_text)} chars): \n{tweet_text}")
        return True
    except tweepy.errors.TooManyRequests:
        logging.warning("Rate limit exceeded. Will not retry.")
        return False
    except tweepy.errors.TweepyException as err:
        logging.error(f"Error posting tweet for {current_city}: {err}")
        # Log specific Twitter error details if available
        if hasattr(err, 'response') and err.response is not None:
            try:
                error_json = err.response.json()
                logging.error(f"Twitter API response error details: {error_json}")
            except json.JSONDecodeError:
                logging.error(f"Twitter API response not JSON: {err.response.text}")
        return False
    except Exception as e:
        logging.error(f"An unexpected error occurred during tweeting for {current_city}: {e}")
        return False

# --- Core Task Logic ---
@app.route('/run-tweet-task', methods=['POST', 'GET'])
def run_tweet_task_endpoint():
    """Main endpoint for a scheduler to call, triggering the tweet task."""
    logging.info("'/run-tweet-task' endpoint triggered by a request.")
    log_data = read_log_file()
    last_posted_city = log_data.get("last_posted_city")
    last_clear_time_utc_str = log_data.get("last_clear_time_utc")

    now_eastern = datetime.now(BOT_TIMEZONE)

    last_clear_time_to_save = None

    if last_clear_time_utc_str:
        last_clear_time_utc = datetime.fromisoformat(last_clear_time_utc_str).replace(tzinfo=pytz.utc)
        last_clear_time_eastern = last_clear_time_utc.astimezone(BOT_TIMEZONE)
        if now_eastern - last_clear_time_eastern >= timedelta(hours=LOG_CLEAR_INTERVAL_HOURS):
            logging.info(f"Log file content is older than {LOG_CLEAR_INTERVAL_HOURS} hours. Clearing log and restarting city cycle.")
            last_posted_city = None
            last_clear_time_to_save = now_eastern.astimezone(pytz.utc).isoformat()
        else:
            last_clear_time_to_save = last_clear_time_utc_str
    else:
        last_clear_time_to_save = now_eastern.astimezone(pytz.utc).isoformat()

    city_to_post = get_next_city(last_posted_city)

    logging.info(f"--- Running weather tweet job for {city_to_post} ---")
    forecast_data = get_weather_forecast(city_to_post)
    if not forecast_data:
        logging.warning(f"Could not retrieve weather for {city_to_post}. Aborting.")
        return jsonify({"status": "error", "message": f"Could not retrieve weather for {city_to_post}."}), 500

    # Call create_weather_tweet_content to get the lines, current_weather_details, and hourly_forecast_data
    tweet_content_parts = create_weather_tweet_content(city_to_post, forecast_data)
    
    # Generate dynamic hashtags separately using the city and forecast_data
    dynamic_hashtags = generate_dynamic_hashtags(city_to_post, forecast_data)
    
    # Create the final tweet_components dictionary to pass to tweet_post
    # This dictionary needs 'lines', 'hashtags', and 'alt_text' for tweet_post to work
    final_tweet_components = {
        'lines': tweet_content_parts['lines'],
        'hashtags': dynamic_hashtags, # Add the generated hashtags here
        'alt_text': create_alt_text_from_forecast(
            city_to_post, 
            tweet_content_parts['current_weather'], 
            tweet_content_parts['hourly_forecast']
        )
    }

    # Now call tweet_post with the correctly assembled dictionary
    success = tweet_post(final_tweet_components, city_to_post)

    if success:
        write_log_file(city_to_post, last_clear_time_to_save)
        logging.info(f"Tweet task for {city_to_post} completed successfully and log updated.")
        return jsonify({"status": "success", "message": f"Tweet task executed successfully for {city_to_post}.", "city": city_to_post}), 200
    else:
        logging.warning(f"Tweet task for {city_to_post} did not complete successfully. Log not updated for this city.")
        return jsonify({"status": "error", "message": f"Tweet task for {city_to_post} failed or was skipped."}), 500

@app.route('/')
def home():
    """A simple endpoint to check if the service is alive."""
    mode = "LIVE MODE" if POST_TO_TWITTER_ENABLED else "TEST MODE"
    log_data = read_log_file()
    last_city = log_data.get("last_posted_city", "N/A")
    last_clear = log_data.get("last_clear_time_utc", "N/A")
    return f"Weather Tweet Bot is alive! Current mode: {mode}. Last posted city: {last_city}. Last log clear (UTC): {last_clear}", 200

# --- Main Execution Block for Local Development ---
if __name__ == "__main__":
    app_port = int(os.environ.get("PORT", 8080))
    logging.info(f"--- Starting Flask Server for local development on port {app_port} ---")
    # Note: debug=True is for development only. Set to False in production.
    app.run(host='0.0.0.0', port=app_port, debug=True)