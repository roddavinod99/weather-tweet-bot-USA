import tweepy
import requests
import os
import pytz
from datetime import datetime, timedelta
from flask import Flask
import logging
import json

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Constants ---
TWITTER_MAX_CHARS = 280
CITIES_TO_MONITOR = ["Chicago", "Phoenix", "Miami", "Orlando", "New York City"]
IMAGE_PATH_RAIN = "its_going_to_rain.png" # This image will now always be attached
POST_TO_TWITTER_ENABLED = os.environ.get("POST_TO_TWITTER_ENABLED", "true").lower() == "true"
LOG_FILE_PATH = "city_tweet_log.json"
LOG_CLEAR_INTERVAL_HOURS = 10 # Interval to clear the log file

if not POST_TO_TWITTER_ENABLED:
    logging.warning("Twitter interactions are DISABLED (Test Mode).")
    logging.warning("To enable, set the environment variable POST_TO_TWITTER_ENABLED=true")
else:
    logging.info("Twitter interactions ARE ENABLED. Tweets will be posted to Twitter.")

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Helper Functions ---
def get_env_variable(var_name, critical=True):
    """Retrieves an environment variable, raising an error if critical and not found."""
    value = os.environ.get(var_name)
    if value is None and critical:
        raise EnvironmentError(f"Critical environment variable '{var_name}' not found.")
    return value

def degrees_to_cardinal(d):
    """Converts wind direction in degrees to a cardinal direction."""
    dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    ix = int((d + 11.25) / 22.5)
    return dirs[ix % 16]

# --- Log File Management Functions ---
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

# --- Initialize Twitter API Clients (v1.1 for media, v2 for tweets) ---
bot_api_client_v2 = None
bot_api_client_v1 = None
try:
    consumer_key = get_env_variable("TWITTER_API_KEY")
    consumer_secret = get_env_variable("TWITTER_API_SECRET")
    access_token = get_env_variable("TWITTER_ACCESS_TOKEN")
    access_token_secret = get_env_variable("TWITTER_ACCESS_TOKEN_SECRET")

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
except Exception as e:
    logging.critical(f"An unexpected error occurred during Twitter client initialization: {e}")

# --- Weather and Tweet Creation Functions ---
def get_weather_forecast(city):
    """Fetches 5-day/3-hour weather forecast data for the specified city."""
    try:
        weather_api_key = get_env_variable("WEATHER_API_KEY")
    except EnvironmentError:
        logging.error("WEATHER_API_KEY not found. Cannot fetch weather.")
        return None

    url = f'https://api.openweathermap.org/data/2.5/forecast?q={city}&appid={weather_api_key}&units=metric'
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as err:
        logging.error(f"Error fetching weather forecast data for {city}: {err}")
        return None

def generate_dynamic_hashtags(weather_data, current_day, city):
    """Generates a list of hashtags based on weather conditions and city."""
    hashtags = {f'#{city.replace(" ", "")}', '#weatherupdate'}

    if not weather_data or 'list' not in weather_data or not weather_data['list']:
        return list(hashtags)

    current_weather = weather_data['list'][0]
    main_conditions = current_weather.get('main', {})
    weather_main_info = current_weather.get('weather', [{}])[0]
    wind_conditions = current_weather.get('wind', {})

    temp_celsius = main_conditions.get('temp', 0)
    sky_description = weather_main_info.get('description', "").lower()
    wind_speed_kmh = wind_conditions.get('speed', 0) * 3.6

    # This section for rain-specific hashtags can still be useful if rain is detected,
    # even if the image is always attached.
    for item in weather_data.get('list', [])[1:5]:
        weather_item_info = item.get('weather', [{}])[0]
        if 'rain' in weather_item_info.get('main', '').lower() or (200 <= weather_item_info.get('id', 800) < 600):
            hashtags.add(f'#{city.replace(" ", "")}Rains')
            hashtags.add('#rain')
            break

    if temp_celsius > 35:
        hashtags.add('#Heatwave')
    if 'clear' in sky_description:
        hashtags.add('#SunnyDay')
    if wind_speed_kmh > 25:
        hashtags.add('#windy')
    if current_day in ['Saturday', 'Sunday']:
        hashtags.add('#WeekendWeather')

    return list(hashtags)


def create_weather_tweet_content(city, forecast_data):
    """
    Creates tweet body, hashtags, and determines if an image should be posted.
    Returns a dictionary with all necessary components for the tweet.
    """
    if not forecast_data or 'list' not in forecast_data or not forecast_data['list']:
        return {"lines": ["Could not generate weather report: Data missing."], "hashtags": ["#error"], "rain_imminent": True, "alt_text": "Weather forecast data missing."} # rain_imminent is now always True

    # Use Eastern Time Zone (America/New_York)
    eastern_tz = pytz.timezone('America/New_York')
    now = datetime.now(eastern_tz)
    current_day = now.strftime('%A')
    # is_rain_forecasted is no longer used to control image attachment,
    # but still calculated for possible rain-specific tweet text/hashtags if desired.
    is_rain_forecasted = False

    # --- Current Weather Details for Alt Text ---
    current_weather = forecast_data['list'][0]
    main_conditions = current_weather.get('main', {})
    wind_conditions = current_weather.get('wind', {})
    weather_info = current_weather.get('weather', [{}])[0]

    sky_description = weather_info.get('description', "N/A").title()
    temp_celsius = main_conditions.get('temp', 0)
    feels_like_celsius = main_conditions.get('feels_like', 0)
    humidity = main_conditions.get('humidity', 0)
    pressure_hpa = main_conditions.get('pressure', 0)
    visibility_km = current_weather.get('visibility', 0) / 1000
    wind_speed_kmh = wind_conditions.get('speed', 0) * 3.6
    wind_direction_cardinal = degrees_to_cardinal(wind_conditions.get('deg', 0))
    cloudiness = current_weather.get('clouds', {}).get('all', 0)


    # --- ALT TEXT GENERATION (Detailed for the next 12 hours) ---
    alt_text_lines = []
    alt_text_lines.append(f"Current weather in {city} at {now.strftime('%I:%M %p %Z')}:")
    alt_text_lines.append(f"It's about {temp_celsius:.0f}°C, but feels like {feels_like_celsius:.0f}°C with {sky_description.lower()} skies. Humidity is {humidity:.0f}%, pressure {pressure_hpa:.0f} hPa. Wind is {wind_speed_kmh:.0f} km/h from the {wind_direction_cardinal}. Visibility around {visibility_km:.0f} km, and cloudiness is {cloudiness:.0f}%.")
    alt_text_lines.append("\n-------------------><-----------------------\n")
    alt_text_lines.append("Here's a detailed 12-hour forecast:") # Updated lead-in for alt text

    # Iterate through the next 4 forecast intervals (12 hours)
    for forecast in forecast_data['list'][1:5]:
        forecast_time_utc = datetime.fromtimestamp(forecast['dt'], tz=pytz.utc)
        forecast_time_local = forecast_time_utc.astimezone(eastern_tz)

        temp = forecast.get('main', {}).get('temp', 0)
        forecast_weather_info = forecast.get('weather', [{}])[0]
        description = forecast_weather_info.get('description', 'N/A').title()
        pop = forecast.get('pop', 0) * 100 # Probability of Precipitation
        rain_volume = forecast.get('rain', {}).get('3h', 0) # Rain volume for 3 hours

        weather_id = forecast_weather_info.get('id', 800)
        if 'rain' in forecast_weather_info.get('main', '').lower() or (200 <= weather_id < 600):
            is_rain_forecasted = True # Still calculate if rain is forecasted

        forecast_detail = f"By {forecast_time_local.strftime('%I %p')}: Expect {description} around {temp:.0f}°C."
        if pop > 0:
            forecast_detail += f" Chance of precipitation: {pop:.0f}%." # Changed to precipitation for accuracy
        if rain_volume > 0:
            forecast_detail += f" ({rain_volume:.1f}mm rain expected)."
        alt_text_lines.append(forecast_detail)

    alt_text_summary = "\n".join(alt_text_lines)
    if len(alt_text_summary) > 1000:
        logging.warning(f"Alt text exceeded 1000 characters ({len(alt_text_summary)}). Truncating.")
        alt_text_summary = alt_text_summary[:997] + "..."

    # --- Main Tweet Content ---
    time_str = now.strftime("%I:%M %p %Z")
    date_str = f"{now.day} {now.strftime('%B')}"
    greeting_line = f"Hello, {city}!👋, {current_day} weather as of {date_str}, {time_str}:"

    tweet_lines = [
        greeting_line,
        f"☁️ Sky: {sky_description}",
        f"🌡️ Temp: {temp_celsius:.0f}°C (feels: {feels_like_celsius:.0f}°C)",
        f"💧 Humidity: {humidity:.0f}%",
        f"💨 Wind: {wind_speed_kmh:.0f} km/h from the {wind_direction_cardinal}",
        "", # Added a blank line for readability
        "Check the attached image's alt text for a detailed 12-hour forecast! ➡️" # New message for alt text guidance
    ]

    # Removed conditional rain messages as image is always attached
    # and alt text guides to detailed forecast.

    closing_message = "Have a great day! 😊"
    tweet_lines.append(closing_message)
    hashtags = generate_dynamic_hashtags(forecast_data, current_day, city)

    return {
        "lines": tweet_lines,
        "hashtags": hashtags,
        "rain_imminent": True, # This is now always True to force image attachment
        "alt_text": alt_text_summary
    }

# --- Tweeting Function ---
def tweet_post(tweet_content):
    """Assembles and posts a tweet, with an image if rain is forecasted (now always attached)."""
    if not all([bot_api_client_v1, bot_api_client_v2]):
        logging.error("Twitter clients not initialized. Aborting tweet post.")
        return False
        
    if not POST_TO_TWITTER_ENABLED:
        logging.info("[TEST MODE] Skipping post.")
        logging.info("Tweet Content:\n" + "\n".join(tweet_content['lines']) + "\n" + " ".join(tweet_content['hashtags']))
        # Log that image *would* be posted, as rain_imminent is always true now
        logging.info(f"[TEST MODE] Would post image '{IMAGE_PATH_RAIN}' with alt text: {tweet_content['alt_text']}")
        return True

    body = "\n".join(tweet_content['lines'])
    hashtags = tweet_content['hashtags']

    full_tweet = f"{body}\n{' '.join(hashtags)}"
    if len(full_tweet) > TWITTER_MAX_CHARS:
        logging.warning("Tweet content + hashtags exceed character limit. Adjusting hashtags.")
        while hashtags and len(f"{body}\n{' '.join(hashtags)}") > TWITTER_MAX_CHARS:
            hashtags.pop()
        tweet_text = f"{body}\n{' '.join(hashtags)}" if hashtags else body
    else:
        tweet_text = full_tweet

    media_ids = []
    # This block will now always execute due to rain_imminent being True
    if tweet_content['rain_imminent']:
        if not os.path.exists(IMAGE_PATH_RAIN):
            logging.error(f"Rain image not found at '{IMAGE_PATH_RAIN}'. Posting tweet without image.")
        else:
            try:
                logging.info(f"Uploading media: {IMAGE_PATH_RAIN}") # Removed 'Rain detected.' as it's always uploaded
                media = bot_api_client_v1.media_upload(filename=IMAGE_PATH_RAIN)
                media_ids.append(media.media_id)
                bot_api_client_v1.create_media_metadata(media_id=media.media_id, alt_text=tweet_content['alt_text'])
                logging.info("Media uploaded and alt text added successfully.")
            except Exception as e:
                logging.error(f"Failed to upload media or add alt text: {e}")

    try:
        bot_api_client_v2.create_tweet(text=tweet_text, media_ids=media_ids if media_ids else None)
        logging.info("Tweet posted successfully to Twitter!")
        logging.info(f"Final Tweet ({len(tweet_text)} chars): \n{tweet_text}")
        return True
    except tweepy.errors.TooManyRequests:
        logging.warning("Rate limit exceeded. Will not retry.")
        return False
    except tweepy.errors.TweepyException as err:
        logging.error(f"Error posting tweet: {err}")
        return False

# --- Core Task Logic ---
def perform_scheduled_tweet_task():
    """Main task to fetch weather, create tweet content, and post it for the next city in sequence."""
    log_data = read_log_file()
    last_posted_city = log_data.get("last_posted_city")
    last_clear_time_utc_str = log_data.get("last_clear_time_utc")

    # Determine current time in Eastern Time Zone for log clearing logic
    eastern_tz = pytz.timezone('America/New_York')
    now_eastern = datetime.now(eastern_tz)

    # Variable to hold the last clear time that will be saved to the log
    last_clear_time_to_save = None

    # Check if log needs to be cleared
    if last_clear_time_utc_str:
        last_clear_time_utc = datetime.fromisoformat(last_clear_time_utc_str).replace(tzinfo=pytz.utc)
        # Convert last_clear_time_utc to Eastern Time for comparison
        last_clear_time_eastern = last_clear_time_utc.astimezone(eastern_tz)
        if now_eastern - last_clear_time_eastern >= timedelta(hours=LOG_CLEAR_INTERVAL_HOURS):
            logging.info(f"Log file content is older than {LOG_CLEAR_INTERVAL_HOURS} hours. Clearing log and restarting city cycle.")
            last_posted_city = None # Reset to start from the beginning of the city list
            last_clear_time_to_save = now_eastern.astimezone(pytz.utc).isoformat() # Update clear time to save
        else:
            last_clear_time_to_save = last_clear_time_utc_str # Retain existing clear time if not clearing
    else:
        # If log was initially empty or corrupt, set current time as the first clear time
        last_clear_time_to_save = now_eastern.astimezone(pytz.utc).isoformat()

    # Determine the next city to tweet
    if last_posted_city is None or last_posted_city not in CITIES_TO_MONITOR:
        next_city_index = 0
    else:
        try:
            current_index = CITIES_TO_MONITOR.index(last_posted_city)
            next_city_index = (current_index + 1) % len(CITIES_TO_MONITOR)
        except ValueError:
            # last_posted_city was not in our list, default to first city
            next_city_index = 0
    
    city_to_post = CITIES_TO_MONITOR[next_city_index]

    logging.info(f"--- Running weather tweet job for {city_to_post} ---")
    forecast_data = get_weather_forecast(city_to_post)
    if not forecast_data:
        logging.warning(f"Could not retrieve weather for {city_to_post}. Aborting.")
        return False

    tweet_content = create_weather_tweet_content(city_to_post, forecast_data)
    success = tweet_post(tweet_content)

    if success:
        # Write the determined last_clear_time_to_save, whether it was updated or retained
        write_log_file(city_to_post, last_clear_time_to_save)
        logging.info(f"Tweet task for {city_to_post} completed successfully and log updated.")
    else:
        logging.warning(f"Tweet task for {city_to_post} did not complete successfully. Log not updated for this city.")
    return success

# --- Flask Routes ---
@app.route('/')
def home():
    """A simple endpoint to check if the service is alive."""
    mode = "LIVE MODE" if POST_TO_TWITTER_ENABLED else "TEST MODE"
    log_data = read_log_file()
    last_city = log_data.get("last_posted_city", "N/A")
    last_clear = log_data.get("last_clear_time_utc", "N/A")
    return f"Weather Tweet Bot is alive! Current mode: {mode}. Last posted city: {last_city}. Last log clear (UTC): {last_clear}", 200

@app.route('/run-tweet-task', methods=['POST', 'GET'])
def run_tweet_task_endpoint():
    """Main endpoint for a scheduler to call, triggering the tweet task."""
    logging.info("'/run-tweet-task' endpoint triggered by a request.")
    success = perform_scheduled_tweet_task()
    if success:
        return "Tweet task executed successfully.", 200
    else:
        return "Tweet task execution failed or was skipped.", 500

# --- Main Execution Block for Local Development ---
if __name__ == "__main__":
    app_port = int(os.environ.get("PORT", 8080))
    logging.info(f"--- Starting Flask Server for local development on port {app_port} ---")
    # Note: debug=True is for development only. Set to False in production.
    app.run(host='0.0.0.0', port=app_port, debug=True)