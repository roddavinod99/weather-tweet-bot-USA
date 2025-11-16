import tweepy
import requests
import os
import pytz
from datetime import datetime
from flask import Flask
import logging
from PIL import Image, ImageDraw, ImageFont
import random

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Helper Functions ---
def get_env_variable(var_name, critical=True):
    """Retrieves an environment variable, raising an error if critical and not found."""
    value = os.environ.get(var_name)
    if value is None and critical:
        # Fixed: Raise EnvironmentError which is more appropriate than just Error
        raise EnvironmentError(f"Critical environment variable '{var_name}' not found.")
    return value

# --- Constants ---
TWITTER_MAX_CHARS = 280
GENERATED_IMAGE_PATH = "weather_report.png"
GIF_DOWNLOAD_PATH = "weather_radar.gif"
GIF_URL = "https://radar.weather.gov/ridge/standard/CONUS_0.gif"

POST_TO_TWITTER_ENABLED = os.environ.get("POST_TO_TWITTER_ENABLED", "false").lower() == "true"

if not POST_TO_TWITTER_ENABLED:
    logging.warning("Twitter interactions are DISABLED (Test Mode).")
    logging.warning("To enable, set the environment variable POST_TO_TWITTER_ENABLED=true")
else:
    logging.info("Twitter interactions ARE ENABLED. Tweets will be posted to Twitter.")

# --- Timezone and City Mapping ---
# This dictionary maps UTC hours to the cities and their timezones.
SCHEDULED_CITIES = {
    0: {"city": "San Francisco", "timezone": "America/Los_Angeles"},
    2: {"city": "New York City", "timezone": "America/New_York"},
    4: {"city": "Los Angeles", "timezone": "America/Los_Angeles"},
    6: {"city": "Chicago", "timezone": "America/Chicago"},
    8: {"city": "Las Vegas", "timezone": "America/Los_Angeles"},
    10: {"city": "Washington", "timezone": "America/New_York"},
    12: {"city": "San Francisco", "timezone": "America/Los_Angeles"},
    14: {"city": "New York City", "timezone": "America/New_York"},
    16: {"city": "Los Angeles", "timezone": "America/Los_Angeles"},
    18: {"city": "Chicago", "timezone": "America/Chicago"},
    20: {"city": "Las Vegas", "timezone": "America/Los_Angeles"},
    22: {"city": "Washington", "timezone": "America/New_York"},
}

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Helper Functions (Continued) ---
def degrees_to_cardinal(d):
    """Converts wind direction in degrees to a cardinal direction."""
    if d is None:
        return "N/A"
    try:
        d = float(d)
    except (ValueError, TypeError):
        return "N/A"
    dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    ix = int((d + 11.25) / 22.5)
    return dirs[ix % 16]

def get_time_based_greeting(hour):
    """Returns 'Good morning', 'Good afternoon', or 'Good evening' based on the hour."""
    if 5 <= hour < 12:
        return "Good morning"
    elif 12 <= hour < 17:
        return "Good afternoon"
    else:
        return "Good evening"
        
def get_weather_mood(temp_f, hour):
    """Generates a dynamic mood phrase based on temperature and time of day."""
    if hour >= 22 or hour < 5:
        return "calm night"
    
    if temp_f > 95:  # ~35°C
        return "warm afternoon" if hour >= 12 else "hot morning"
    elif temp_f < 68: # ~20°C
        return "cool morning" if hour < 12 else "chilly afternoon"
    else:
        return "pleasant day"

def generate_air_quality_text(city, aqi_str, uvi, uvi_level):
    """Generates dynamic text for air quality and UV index. (Not used in the final tweet/image content but kept for completeness)."""
    
    # Air quality sentence
    if aqi_str == "good":
        aqi_text = f"The air quality in {city} is currently {aqi_str}, which is great news for outdoor activities."
    else:
        aqi_text = f"The air quality in {city} is currently {aqi_str}. It's a good idea to be mindful of this if you have respiratory sensitivities."
    
    # UV index sentence
    uvi_text = f"The UV Index is {uvi} out of 11 ({uvi_level})."
    if uvi <= 2:
        uvi_text += " You don't have to worry too much about sun exposure today."
    elif uvi <= 5:
        uvi_text += " A little sunscreen wouldn't hurt, especially if you'll be outside for a while."
    else:
        uvi_text += " Be sure to use sun protection like sunscreen and a hat."
        
    return f"{aqi_text} {uvi_text}"

# --- Initialize Twitter API Clients (v1.1 for media, v2 for tweets) ---
bot_api_client_v2 = None
bot_api_client_v1 = None
try:
    consumer_key = get_env_variable("TWITTER_API_KEY")
    consumer_secret = get_env_variable("TWITTER_API_SECRET")
    access_token = get_env_variable("TWITTER_ACCESS_TOKEN")
    access_token_secret = get_env_variable("TWITTER_ACCESS_TOKEN_SECRET")
    
    # Tweepy v2 Client initialization for read/write actions
    # Using the correct parameter names for OAuth 1.0a User Context
    bot_api_client_v2 = tweepy.Client(
        consumer_key=consumer_key, consumer_secret=consumer_secret,
        access_token=access_token, access_token_secret=access_token_secret
    )
    # Tweepy v1.1 Client initialization for media upload (requires OAuth1UserHandler)
    auth = tweepy.OAuth1UserHandler(consumer_key, consumer_secret, access_token, access_token_secret)
    bot_api_client_v1 = tweepy.API(auth)
    logging.info("Twitter v1.1 and v2 clients initialized successfully.")
except EnvironmentError as e:
    logging.error(f"Error initializing Twitter clients due to missing environment variable: {e}")
    # Set clients to None in case of partial failure
    bot_api_client_v2 = None
    bot_api_client_v1 = None
except Exception as e:
    logging.critical(f"An unexpected error occurred during Twitter client initialization: {e}")
    # Set clients to None in case of unexpected failure
    bot_api_client_v2 = None
    bot_api_client_v1 = None

# --- Weather and Data Fetching Functions ---
def get_city_coordinates(city, api_key):
    """Fetches latitude and longitude for a city using OpenWeatherMap Geocoding API."""
    url = f"http://api.openweathermap.org/geo/1.0/direct?q={city},US&limit=1&appid={api_key}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data and len(data) > 0:
            return data[0]['lat'], data[0]['lon']
        else:
            logging.error(f"Could not find coordinates for city: {city}")
            return None, None
    except requests.exceptions.RequestException as err:
        logging.error(f"Error fetching coordinates for {city}: {err}")
        return None, None

def get_one_call_weather_data(lat, lon, api_key):
    """
    Fetches weather data using OpenWeatherMap One Call API 3.0 in imperial units,
    including the daily forecast data.
    """
    if not lat or not lon:
        return None
    # We exclude 'minutely' and 'alerts'
    url = f"https://api.openweathermap.org/data/3.0/onecall?lat={lat}&lon={lon}&appid={api_key}&units=imperial&exclude=minutely,alerts"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as err:
        logging.error(f"Error fetching One Call weather data: {err}")
        return None

def get_air_pollution_data(lat, lon, api_key):
    """Fetches air pollution data (AQI)."""
    if not lat or not lon:
        return None
    url = f"http://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={lon}&appid={api_key}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as err:
        logging.error(f"Error fetching air pollution data: {err}")
        return None

def download_weather_gif(gif_url=GIF_URL, output_path=GIF_DOWNLOAD_PATH):
    """
    Downloads the weather radar GIF from the provided URL.
    Returns the path to the downloaded GIF if successful, None otherwise.
    """
    try:
        logging.info(f"Downloading GIF from {gif_url}")
        response = requests.get(gif_url, timeout=15)
        response.raise_for_status()
        
        with open(output_path, 'wb') as f:
            f.write(response.content)
        
        logging.info(f"GIF downloaded successfully to {output_path}")
        return output_path
    except requests.exceptions.RequestException as err:
        logging.error(f"Error downloading GIF: {err}")
        return None
    except IOError as err:
        logging.error(f"Error saving GIF to file: {err}")
        return None
def generate_dynamic_hashtags(city, weather_data, local_date_time):
    """Generates a list of hashtags based on weather conditions."""
    city_no_space = city.replace(" ", "")
    hashtags = {f'#{city_no_space}', '#weatherupdate', '#USWeather'}

    if not weather_data or 'current' not in weather_data:
        logging.warning("No weather data available for hashtag generation.")
        return list(hashtags)

    current_weather = weather_data['current']
    temp_fahrenheit = current_weather.get('temp', 0)
    sky_description = current_weather.get('weather', [{}])[0].get('description', "").lower()
    wind_speed_mph = current_weather.get('wind_speed', 0)
    current_day = local_date_time.strftime('%A')
    
    rain_forecasted_in_12_hours = any(
        hour.get('pop', 0) > 0.1 # Probability of precipitation greater than 10%
        for hour in weather_data.get('hourly', [])[:12]
    )

    if rain_forecasted_in_12_hours:
        hashtags.add(f'#{city_no_space}Rains')
        hashtags.add('#RainAlert')
    if temp_fahrenheit > 95:
        hashtags.add('#Heatwave')
    elif temp_fahrenheit < 59:
        hashtags.add('#ColdWeather')
    if 'clear' in sky_description:
        hashtags.add('#SunnyDay')
    elif 'cloud' in sky_description:
        hashtags.add('#Cloudy')
    if wind_speed_mph > 20:  
        hashtags.add('#Windy')
    if current_day in ['Saturday', 'Sunday']:
        hashtags.add('#WeekendWeather')
    
    return list(hashtags)

def create_weather_tweet_content(city, weather_data, air_pollution_data, local_date_time, local_timezone):
    """
    Creates the conversational tweet content and the full text content for the image.
    """
    if not weather_data or 'current' not in weather_data or 'hourly' not in weather_data or 'daily' not in weather_data:
        logging.error("Missing or invalid weather data for tweet content creation.")
        return {"lines": ["Could not generate weather report: Data missing."], "hashtags": ["#error"], "alt_text": "", "image_content": ["No weather data available."]}

    current_day = local_date_time.strftime('%A')
    current_hour = local_date_time.hour

    # --- Extract Current Weather Data ---
    current = weather_data['current']
    temp_f = current.get('temp')
    feels_like_f = current.get('feels_like')
    humidity = current.get('humidity')
    wind_speed_mph = current.get('wind_speed')
    wind_direction_deg = current.get('wind_deg')
    uvi = current.get('uvi')
    sky_description_now = current.get('weather', [{}])[0].get('description', 'clouds').title()
    
    # --- Check for future rain to make text dynamic ---
    hourly_forecasts = weather_data.get('hourly', [])
    pop_values = [hour.get('pop', 0) for hour in hourly_forecasts[:12]]
    max_pop_in_12_hours = max(pop_values) if pop_values else 0
    pop_str_max = f"{max_pop_in_12_hours * 100:.0f}%" # Use max pop for the summary sentence (FIXED LOGICAL ERROR)
    
    # --- Data Conversion and Formatting ---
    temp_f_str = f"{temp_f:.0f}°F" if temp_f is not None else "N/A"
    feels_like_f_str = f"{feels_like_f:.0f}°F" if feels_like_f is not None else "N/A"
    humidity_str = f"{humidity:.0f}%" if humidity is not None else "N/A"
    wind_speed_mph_str = f"{wind_speed_mph:.0f} mph" if wind_speed_mph is not None else "N/A"
    wind_direction_cardinal = degrees_to_cardinal(wind_direction_deg)
    
    # --- Air Quality Data ---
    aqi_str = "moderate" # Default
    if air_pollution_data and 'list' in air_pollution_data and air_pollution_data['list']:
        aqi = air_pollution_data['list'][0]['main']['aqi']
        aqi_map = {1: "good", 2: "fair", 3: "moderate", 4: "poor", 5: "very poor"}
        aqi_str = aqi_map.get(aqi, "moderate")
    
    # --- UV Index Data ---
    uvi_level = "N/A"
    if uvi is not None:
        if uvi <= 2: uvi_level = "low"
        elif uvi <= 5: uvi_level = "moderate"
        elif uvi <= 7: uvi_level = "high"
        else: uvi_level = "very high"

    # --- ALT TEXT AND IMAGE CONTENT GENERATION ---
    greeting = get_time_based_greeting(current_hour)
    time_str = local_date_time.strftime('%I:%M %p')
    date_str = f"{local_date_time.day} {local_date_time.strftime('%B')}"

    image_text_lines = []
    
    image_text_lines.append(f"Weather Update for {city.title()} City!")
    image_text_lines.append(f"As of {time_str}, {date_str}")
    image_text_lines.append("")
    
    image_text_lines.append("Current Conditions:")
    image_text_lines.append(f"Temperature: {temp_f_str} (feels like {feels_like_f_str})")
    image_text_lines.append(f"Weather: {sky_description_now}")
    image_text_lines.append(f"Humidity: {humidity_str}")
    image_text_lines.append(f"Wind: {wind_direction_cardinal} at {wind_speed_mph_str}")
    image_text_lines.append("")
    
    weather_mood = get_weather_mood(temp_f, current_hour)
    main_paragraph_intro = f"The city is experiencing a {weather_mood}."
    rain_sentence = ""
    # Use max_pop_in_12_hours for a more accurate summary (FIXED LOGICAL ERROR)
    if max_pop_in_12_hours > 0.5:
        rain_sentence = f"There's a high chance of rain today (Max {pop_str_max}), so don't forget your umbrella!"
    elif max_pop_in_12_hours > 0.1:
        rain_sentence = f"There's a small chance of rain today (Max {pop_str_max}), so keeping an umbrella handy might be a good idea."
    else:
        rain_sentence = f"With a low chance of rain (Max {pop_str_max}), you can likely leave your umbrella at home."
        
    image_text_lines.append(f"Today's Outlook: {main_paragraph_intro} {rain_sentence}")
    image_text_lines.append(f"Air Quality: {aqi_str.title()}. UV Index: {uvi} ({uvi_level.title()})")
    image_text_lines.append("")
    
    image_text_lines.append("Detailed Hourly Forecast (Next 12h):")
    # Loop over the next 12 hours, stepping by 3 for a concise summary
    for i in range(3, 13, 3):
        if i < len(hourly_forecasts):
            hour_data = hourly_forecasts[i]
            # Use the datetime object from the forecast data itself for accuracy.
            forecast_time = datetime.fromtimestamp(hour_data['dt'], tz=local_date_time.tzinfo) 
            pop_hourly = hour_data.get('pop', 0)
            temp_hourly = hour_data.get('temp')
            description = hour_data.get('weather', [{}])[0].get('description', '').title()
            
            time_str_hourly = forecast_time.strftime('%I %p')
            temp_hourly_str = f"{temp_hourly:.0f}°F" if temp_hourly is not None else ""
            
            # OpenWeatherMap uses '1h' for the last hour's accumulation
            rain_inch = hour_data.get('rain', {}).get('1h', 0)
            snow_inch = hour_data.get('snow', {}).get('1h', 0)
            precipitation_str = ""
            if rain_inch > 0:
                precipitation_str = f"(Rain: {rain_inch:.2f} in)"
            elif snow_inch > 0:
                precipitation_str = f"(Snow: {snow_inch:.2f} in)"
            else:
                precipitation_str = "(Precipitation: 0 in)"
            
            detail_str = f"By {time_str_hourly}: {description} at {temp_hourly_str}. Rain chance: {pop_hourly * 100:.0f}%. {precipitation_str}"
            image_text_lines.append(detail_str)
            
    image_text_lines.append("")
    
    image_text_lines.append("Upcoming 3-Day Forecast:")
    daily_forecasts = weather_data.get('daily', [])
    for i in range(1, min(4, len(daily_forecasts))):
        day_data = daily_forecasts[i]
        # Use the local timezone from the passed in local_date_time
        forecast_date = datetime.fromtimestamp(day_data['dt'], tz=local_date_time.tzinfo) 
        day_of_week = forecast_date.strftime('%A')
        temp_min = day_data.get('temp', {}).get('min')
        temp_max = day_data.get('temp', {}).get('max')
        description = day_data.get('weather', [{}])[0].get('description', '').title()
        
        temp_min_str = f"{temp_min:.0f}°F" if temp_min is not None else "N/A"
        temp_max_str = f"{temp_max:.0f}°F" if temp_max is not None else "N/A"
        
        day_summary = f"{day_of_week}: High {temp_max_str}, Low {temp_min_str}. Expect {description}."
        image_text_lines.append(day_summary)
        
    image_text_lines.append("")
    
    # Check for future rain in the next 12 hours to drive the closing sentence
    future_rain_in_12_hours = max_pop_in_12_hours > 0.1
    closing_sentence = ""
    if future_rain_in_12_hours:
        closing_sentence = f"Stay safe, drive carefully on the wet roads!"
    else:
        closing_sentence = f"Stay safe and enjoy your day!"

    image_text_lines.append(closing_sentence)
    
    full_alt_text = "\n".join(image_text_lines)

    # --- Main Tweet Content (A shorter summary) ---
    greeting_line = f"{greeting.title()}, {city}! 👋, {current_day} weather as of {date_str}, {time_str}:"

    tweet_lines = [
        greeting_line,
        f"It's currently {temp_f_str} (feels like {feels_like_f_str}) with {sky_description_now}.",
        f"AQI is {aqi_str}. #StaySafe"
    ]

    hashtags = generate_dynamic_hashtags(city, weather_data, local_date_time)
    
    return {
        "lines": tweet_lines,
        "hashtags": hashtags,
        "alt_text": full_alt_text,
        "image_content": image_text_lines,
    }

def create_weather_image(image_text_lines, output_path=GENERATED_IMAGE_PATH):
    """
    Generates an image with the weather report text from a list of lines,
    with bold headings and text wrapping.
    """
    try:
        # Increased dimensions to hold more text
        img_width, img_height = 985, 690  
        bg_color, text_color = (52, 52, 52), (252, 230, 207)
        # Note: Footer updated to remove chart reference.
        footer_text = "Detailed Weather Report. Data by OpenWeatherMap API"

        img = Image.new('RGB', (img_width, img_height), color=bg_color)
        d = ImageDraw.Draw(img)

        script_dir = os.path.dirname(os.path.abspath(__file__))
        # Assuming Consolas or similar monospace fonts are available or using fallback
        font_regular_path = os.path.join(script_dir, "consolas.ttf")
        font_bold_path = os.path.join(script_dir, "consolasb.ttf")

        try:
            font_size = 18  
            font_regular = ImageFont.truetype(font_regular_path, font_size)
            font_bold = ImageFont.truetype(font_bold_path, font_size)
            footer_font = ImageFont.truetype(font_regular_path, 14)
            logging.info("Successfully loaded Consolas fonts.")
        except IOError:
            logging.warning("Custom fonts not found. Using default font.")
            font_regular = ImageFont.load_default()
            font_bold = font_regular
            footer_font = ImageFont.load_default()
            font_size = 10
            
        line_height = font_size + 7
        # Removed 'Chart' and added 'Weather Update' as a new heading
        heading_prefixes = ("Weather Update", "Current Conditions:", "Today's Outlook:", "Detailed Hourly Forecast", "Upcoming 3-Day Forecast")
        padding_x, padding_y = 20, 20
        max_text_width = img_width - (2 * padding_x)
        y_text = padding_y

        for original_line in image_text_lines:
            if not original_line.strip():
                y_text += line_height
                continue

            is_heading = False
            for prefix in heading_prefixes:
                if original_line.strip().startswith(prefix):
                    is_heading = True
                    break
                    
            current_font = font_bold if is_heading else font_regular
            words = original_line.split(' ')
            current_line_words = []
            
            for word in words:
                test_line = ' '.join(current_line_words + [word])
                # Use textlength for accurate wrapping
                text_w = d.textlength(test_line, font=current_font)

                if text_w <= max_text_width:
                    current_line_words.append(word)
                else:
                    if current_line_words:
                        d.text((padding_x, y_text), ' '.join(current_line_words), font=current_font, fill=text_color)
                        y_text += line_height
                    current_line_words = [word]

            if current_line_words:
                d.text((padding_x, y_text), ' '.join(current_line_words), font=current_font, fill=text_color)
                y_text += line_height

            # Stop if we are too close to the footer area
            if y_text >= img_height - padding_y - (footer_font.size * 2):
                logging.warning("Image content exceeded image height. Truncating.")
                break

        # Draw the centered footer at the bottom
        footer_bbox = d.textbbox((0, 0), footer_text, font=footer_font)
        footer_width = footer_bbox[2] - footer_bbox[0]
        footer_x = (img_width - footer_width) / 2
        footer_y = img_height - padding_y - footer_font.size
        d.text((footer_x, footer_y), footer_text, font=footer_font, fill=text_color)

        img.save(output_path)
        logging.info(f"Weather image created successfully at {output_path}")
        return output_path
    except Exception as e:
        logging.error(f"Error creating weather image: {e}")
        return None


# --- Tweeting Function ---
def tweet_post(tweet_content, city):
    """
    Assembles and posts a tweet with the generated text report image and weather GIF.
    The image and GIF are *not* deleted in Test Mode.
    """
    if not all([bot_api_client_v1, bot_api_client_v2]):
        logging.error("Twitter clients not initialized. Aborting tweet post.")
        return False
    
    generated_image_path = create_weather_image(tweet_content['image_content'])
    
    # Check if image creation failed
    if not generated_image_path:
        logging.error("Image generation failed. Aborting media tweet.")
        if not POST_TO_TWITTER_ENABLED:
            logging.info("[TEST MODE] Skipping actual Twitter post. Image creation failed.")
            return True
    
    # Download the weather GIF
    gif_path = download_weather_gif()
    if not gif_path:
        logging.warning("GIF download failed. Will continue with image only.")
    
    if not POST_TO_TWITTER_ENABLED:
        logging.info("[TEST MODE] Skipping actual Twitter post.")
        logging.info("Tweet Content:\n" + "\n".join(tweet_content['lines']) + "\n" + " ".join(tweet_content['hashtags']))
        
        if generated_image_path:
            logging.info(f"Generated text image for inspection: {generated_image_path}")
        if gif_path:
            logging.info(f"Downloaded weather GIF for inspection: {gif_path}")
            
        logging.info(f"Media files were NOT deleted. Check the project directory.")
        return True

    # --- LIVE MODE (POST_TO_TWITTER_ENABLED is true) ---
    
    body = "\n".join(tweet_content['lines'])
    hashtags = tweet_content['hashtags']
    full_tweet = f"{body}\n{' '.join(hashtags)}"

    if len(full_tweet) > TWITTER_MAX_CHARS:
        logging.warning("Tweet content exceeds character limit. Adjusting.")
        while hashtags and len(f"{body}\n{' '.join(hashtags)}") > TWITTER_MAX_CHARS:
            hashtags.pop()
        tweet_text = f"{body}\n{' '.join(hashtags)}"
        if len(tweet_text) > TWITTER_MAX_CHARS:
            tweet_text = tweet_text[:TWITTER_MAX_CHARS - 3] + "..."
    else:
        tweet_text = full_tweet
    
    media_ids = []
    
    # Upload Text Image
    if generated_image_path and os.path.exists(generated_image_path):
        try:
            logging.info(f"Uploading media: {generated_image_path}")
            media = bot_api_client_v1.media_upload(filename=generated_image_path)
            media_ids.append(media.media_id)
            alt_text = tweet_content['alt_text']
            if len(alt_text) > 1000:
                alt_text = alt_text[:997] + "..."
            bot_api_client_v1.create_media_metadata(media_id=media.media_id_string, alt_text=alt_text)
            logging.info("Text image uploaded and alt text added successfully.")
        except Exception as e:
            logging.error(f"Failed to upload text image or add alt text: {e}")
    
    # Upload Weather GIF
    if gif_path and os.path.exists(gif_path):
        try:
            logging.info(f"Uploading media: {gif_path}")
            media = bot_api_client_v1.media_upload(filename=gif_path, media_category="tweet_gif")
            media_ids.append(media.media_id)
            logging.info("Weather GIF uploaded successfully.")
        except Exception as e:
            logging.error(f"Failed to upload weather GIF: {e}")
    
    # Cleanup temporary files (Only in LIVE mode)
    if generated_image_path and os.path.exists(generated_image_path):
        try:
            os.remove(generated_image_path)
            logging.debug(f"Removed temporary file: {generated_image_path}")
        except OSError as e:
            logging.warning(f"Error removing temporary image file {generated_image_path}: {e}")
    
    if gif_path and os.path.exists(gif_path):
        try:
            os.remove(gif_path)
            logging.debug(f"Removed temporary file: {gif_path}")
        except OSError as e:
            logging.warning(f"Error removing temporary GIF file {gif_path}: {e}")

    if not media_ids and generated_image_path:
        logging.warning("No images were successfully uploaded. Posting tweet without media.")
        
    try:
        # Twitter V2 supports up to 4 media items per tweet.
        response = bot_api_client_v2.create_tweet(text=tweet_text, media_ids=media_ids if media_ids else None)
        logging.info(f"Tweet posted successfully! Tweet ID: {response.data['id']}")
        return True
    except tweepy.errors.TweepyException as err:
        logging.error(f"Error posting tweet: {err}")
        return False
    except Exception as e:
        logging.critical(f"An unexpected error occurred during tweet posting: {e}")
        return False

# -------------------------------------------------------------
# --- Core Task Logic - MODIFIED FOR RANDOM CITY IN TEST MODE ---
# -------------------------------------------------------------
def perform_scheduled_tweet_task():
    """Main task to fetch data, create content, and post the tweet based on UTC hour or randomly in test mode."""
    logging.info("--- Starting scheduled weather tweet job ---")
    
    city_data = None
    
    if not POST_TO_TWITTER_ENABLED:
        # TEST MODE: Pick a random city regardless of time
        all_cities = list(SCHEDULED_CITIES.values())
        city_data = random.choice(all_cities)
        logging.info(f"TEST MODE: Randomly selected city: {city_data['city']}")
    else:
        # LIVE MODE: Check scheduled time
        current_utc_hour = datetime.now(pytz.utc).hour
        logging.info(f"LIVE MODE: Current UTC hour is {current_utc_hour}")
        
        # Check if a city is scheduled for this UTC hour
        if current_utc_hour not in SCHEDULED_CITIES:
            logging.info(f"No city scheduled for UTC hour {current_utc_hour}. Skipping this run.")
            return True
            
        city_data = SCHEDULED_CITIES[current_utc_hour]
    
    city_to_monitor = city_data["city"]
    city_timezone_str = city_data["timezone"]
    try:
        city_timezone = pytz.timezone(city_timezone_str)
    except pytz.exceptions.UnknownTimeZoneError:
        logging.error(f"Unknown timezone: {city_timezone_str}. Aborting.")
        return False

    # Get the current local time for the city (used for dynamic greeting, forecast, etc.)
    local_time = datetime.now(city_timezone)
    logging.info(f"Processing city: {city_to_monitor}. Local time is {local_time.strftime('%I:%M %p, %A, %B %d, %Y')}")
    
    try:
        weather_api_key = get_env_variable("WEATHER_API_KEY")
    except EnvironmentError:
        logging.error("WEATHER_API_KEY not found. Aborting.")
        return False

    lat, lon = get_city_coordinates(city_to_monitor, weather_api_key)
    if not lat or not lon:
        return False

    weather_data = get_one_call_weather_data(lat, lon, weather_api_key)
    air_pollution_data = get_air_pollution_data(lat, lon, weather_api_key)

    if not weather_data:
        logging.warning(f"Could not retrieve weather for {city_to_monitor}. Aborting.")
        return False

    # Pass the local time and timezone to the tweet content creation function
    tweet_content = create_weather_tweet_content(city_to_monitor, weather_data, air_pollution_data, local_time, city_timezone_str)
    
    if "Could not generate weather report" in tweet_content['lines'][0]:
        logging.error("Tweet content generation failed. Aborting tweet post.")
        return False

    success = tweet_post(tweet_content, city_to_monitor)
    if success:
        logging.info(f"Tweet task for {city_to_monitor} completed successfully.")
    else:
        logging.warning(f"Tweet task for {city_to_monitor} did not complete successfully.")
    return success

# --- Flask Routes ---
@app.route('/')
def home():
    """A simple endpoint to check if the service is alive."""
    mode = "LIVE MODE" if POST_TO_TWITTER_ENABLED else "TEST MODE"
    return f"Weather Tweet Bot is alive! Current mode: {mode}", 200

@app.route('/run-tweet-task', methods=['POST', 'GET'])
def run_tweet_task_endpoint():
    """Main endpoint for a scheduler to call, triggering the tweet task."""
    logging.info("'/run-tweet-task' endpoint triggered by a request.")
    success = perform_scheduled_tweet_task()
    if success:
        return "Tweet task executed successfully.", 200
    else:
        return "Tweet task execution failed or was skipped.", 500

# --- Main Execution Block ---
if __name__ == "__main__":
    app_port = int(os.environ.get("PORT", 8080))
    is_debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    logging.info(f"--- Starting Flask Server on port {app_port} ---")
    logging.info(f"Debug mode is {'ON' if is_debug_mode else 'OFF'}")
    app.run(host='0.0.0.0', port=app_port, debug=is_debug_mode)