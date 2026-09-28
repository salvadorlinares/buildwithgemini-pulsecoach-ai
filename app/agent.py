# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import json
import os
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.cloud import firestore, storage
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors.agent_engine_sandbox_code_executor import AgentEngineSandboxCodeExecutor
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.genai import types

try:
    from .a2ui_utils import a2ui_callback
except ImportError:
    from a2ui_utils import a2ui_callback

# Load local environment variables (.env)
load_dotenv()

# Hardcoded GCP Project ID and Location for Firestore and Vertex AI Memory Bank
PROJECT_ID = "qwiklabs-gcp-01-2d29658a1c8c"
LOCATION = "us-east1"

# Load Agent Engine resource name from deployment_metadata.json if present
REMOTE_AGENT_RUNTIME_ID = None
metadata_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "deployment_metadata.json")
if os.path.exists(metadata_path):
    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)
            REMOTE_AGENT_RUNTIME_ID = metadata.get("remote_agent_runtime_id")
    except Exception:
        pass

# Extract Agent Engine ID for Memory Bank (uses deployed ID when on Agent Runtime)
MEMORY_BANK_ID = REMOTE_AGENT_RUNTIME_ID.split("/")[-1] if REMOTE_AGENT_RUNTIME_ID else "3365726038901391360"

# Initialize AgentEngineSandboxCodeExecutor for Agent Platform sandboxed Python code execution
code_executor = AgentEngineSandboxCodeExecutor(
    agent_engine_resource_name=REMOTE_AGENT_RUNTIME_ID
)


# WRITE: After each turn, send the session events to Vertex AI Memory Bank for extraction (including user allergies and preferences)
async def generate_memories_callback(callback_context: CallbackContext):
    await callback_context.add_session_to_memory()
    return None


# Memory service builder function for future redeployment on Agent Runtime
def memory_bank_service_builder():
    return VertexAiMemoryBankService(
        project=PROJECT_ID,
        location=LOCATION,
        agent_engine_id=MEMORY_BANK_ID,
    )


# Build A2UI system prompt using A2uiSchemaManager version 0.8 and BasicCatalog (version 0.8 only)
schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are PulseCoach AI, a personal fitness & wellness coach. "
        "You pay special attention to and remember all user allergies (e.g., food allergies, drug allergies, environmental allergies, dietary restrictions) and health conditions. "
        "You remember the user's stated preferences, fitness goals, medical background, allergies, and facts from previous conversations, and use them to personalize your responses, provide safe workout plans, and explicitly warn against or avoid any known allergens. "
        "You assist users by searching exercises from their Firestore catalog, logging workout sessions, "
        "generating visual workout badges via gemini-3.1-flash-lite-image, "
        "geocoding addresses and finding nearby gyms/fitness centers via Google Maps and Places APIs, "
        "fetching external exercise routines from the wger API, looking up nutrition data for foods via Open Food Facts, "
        "calculating training heart rate zones and daily metabolic targets, running sandboxed Python code for custom data analysis, and providing personalized wellness advice."
    ),
    workflow_description="Analyze the request, call tools as needed, and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        "{\"Image\": {\"url\": {\"literalString\": \"https://...\"}}}. Never point an "
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)


def generate_workout_badge(
    achievement_title: str,
    description: str = "",
    tool_context: ToolContext = None,
) -> str:
    """Generates an image for a fitness achievement badge using gemini-3.1-flash-lite-image in the global region.
    Saves the image as an ADK session artifact via tool_context and uploads it to public Cloud Storage, returning its public HTTPS URL.

    Args:
        achievement_title: Title of the badge or fitness achievement (e.g. "10K Finisher", "Iron Squat Champion", "30-Day Streak").
        description: Optional visual theme or style details.
        tool_context: The ADK ToolContext instance (injected automatically).

    Returns:
        The public GCS HTTPS URL of the generated image.
    """
    client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")
    prompt = f"A vibrant, high-quality fitness achievement badge design for '{achievement_title}'. {description}".strip()

    try:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=prompt,
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )

        candidates = getattr(response, "candidates", [])
        if not candidates or not candidates[0].content.parts:
            return f"Error: No image content returned for badge '{achievement_title}'."

        part = candidates[0].content.parts[0]
        image_bytes = part.inline_data.data
        mime_type = part.inline_data.mime_type or "image/jpeg"

        ext = "jpg" if "jpeg" in mime_type else "png"
        timestamp_str = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        filename = f"badge_{timestamp_str}.{ext}"

        # 1. Save artifact in ToolContext for Playground Artifacts panel
        if tool_context:
            artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
            tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # 2. Upload image bytes to public Cloud Storage bucket
        bucket_name = "pulsecoach-media-qwiklabs-gcp-01-2d29658a1c8c"
        gcs_key = f"generated/{filename}"

        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(gcs_key)
        blob.upload_from_string(image_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{bucket_name}/{gcs_key}"
        return public_url
    except Exception as e:
        return f"Error generating workout badge image: {str(e)}"


def geocode_address(address: str) -> str:
    """Converts an address, landmark, or city name into latitude and longitude coordinates using the Google Maps Geocoding API.

    Args:
        address: The address or place to geocode (e.g. "San Francisco, CA" or "1600 Amphitheatre Pkwy, Mountain View, CA").

    Returns:
        Formatted address and exact latitude/longitude coordinates.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        return "Error: GOOGLE_MAPS_API_KEY environment variable is not set."

    encoded_address = urllib.parse.quote(address)
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={encoded_address}&key={api_key}"

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            results = data.get("results", [])
            if not results:
                return f"No geocoding results found for '{address}'."

            first_result = results[0]
            formatted_address = first_result.get("formatted_address", address)
            location = first_result.get("geometry", {}).get("location", {})
            lat = location.get("lat")
            lng = location.get("lng")

            return (
                f"📍 Geocoding Results for '{address}':\n"
                f"  Formatted Address: {formatted_address}\n"
                f"  Coordinates: Latitude {lat}, Longitude {lng}"
            )
    except Exception as e:
        return f"Error geocoding address '{address}': {str(e)}"


def find_nearby_places(
    latitude: float,
    longitude: float,
    place_type: str = "gym",
    radius_meters: float = 1500.0,
) -> str:
    """Finds nearby places of a given type around a location using the Google Places API (New) REST searchNearby endpoint.

    Args:
        latitude: Latitude coordinate of the central location.
        longitude: Longitude coordinate of the central location.
        place_type: Type of place to search for (e.g. "gym", "fitness_center", "park", "sports_complex").
        radius_meters: Search radius in meters (default: 1500.0 meters).

    Returns:
        A formatted list of nearby places including name, address, and location coordinates.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        return "Error: GOOGLE_MAPS_API_KEY environment variable is not set."

    # Google Places API (New) searchNearby REST endpoint
    url_new = "https://places.googleapis.com/v1/places:searchNearby"
    headers_new = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location,places.primaryType",
    }
    body_new = json.dumps({
        "includedTypes": [place_type],
        "maxResultCount": 5,
        "locationRestriction": {
            "circle": {
                "center": {"latitude": latitude, "longitude": longitude},
                "radius": radius_meters,
            }
        },
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url_new, data=body_new, headers=headers_new, method="POST")
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            places = data.get("places", [])
            if not places:
                return f"No nearby '{place_type}' places found within {radius_meters}m."

            results = []
            for p in places:
                name = p.get("displayName", {}).get("text", "Unnamed Place")
                addr = p.get("formattedAddress", "No address available")
                loc = p.get("location", {})
                lat_val, lng_val = loc.get("latitude"), loc.get("longitude")
                results.append(f"🏋️ {name}\n  Address: {addr}\n  Location: ({lat_val}, {lng_val})")

            return "\n\n".join(results)
    except Exception:
        # Fallback to Places API Web Service nearbysearch
        fallback_url = f"https://maps.googleapis.com/maps/api/place/nearbysearch/json?location={latitude},{longitude}&radius={radius_meters}&type={place_type}&key={api_key}"
        try:
            with urllib.request.urlopen(fallback_url, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                results_list = data.get("results", [])
                if not results_list:
                    return f"No nearby '{place_type}' places found within {radius_meters}m."
                formatted = []
                for item in results_list[:5]:
                    name = item.get("name", "Unnamed Place")
                    addr = item.get("vicinity", "No address available")
                    loc = item.get("geometry", {}).get("location", {})
                    lat_val, lng_val = loc.get("lat"), loc.get("lng")
                    formatted.append(f"🏋️ {name}\n  Address: {addr}\n  Location: ({lat_val}, {lng_val})")
                return "\n\n".join(formatted)
        except Exception as fb_err:
            return f"Error finding nearby places: {str(fb_err)}"


def fetch_external_exercises(category_name: str = "") -> str:
    """Fetches real-world public exercise definitions from the external wger Workout Manager API.

    Args:
        category_name: Optional category filter (e.g. "Abs", "Shoulders", "Arms", "Chest").

    Returns:
        A list of exercises fetched live from the wger public API.
    """
    api_key = os.environ.get("WGER_API_KEY", "")
    url = "https://wger.de/api/v2/exerciseinfo/?limit=20"
    headers = {"User-Agent": "PulseCoachAI-Agent/1.0"}
    if api_key:
        headers["Authorization"] = f"Token {api_key}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            results = data.get("results", [])
            formatted = []
            for item in results:
                cat = item.get("category", {}).get("name", "")
                if category_name and category_name.lower() not in cat.lower():
                    continue

                translations = item.get("translations", [])
                name = "Unknown"
                desc = ""
                for t in translations:
                    if t.get("language") == 2:  # English translation
                        name = t.get("name", name)
                        desc = t.get("description_source", "") or t.get("description", "")
                        break
                if name == "Unknown" and translations:
                    name = translations[0].get("name", "Unknown")

                equipment = ", ".join([eq.get("name", "") for eq in item.get("equipment", [])]) or "Bodyweight"
                clean_desc = desc.replace("<p>", "").replace("</p>", "").replace("\n", " ").strip()
                formatted.append(f"🏋️ {name} (Category: {cat}, Equipment: {equipment})\n  {clean_desc[:120]}...")

                if len(formatted) >= 5:
                    break

            if not formatted:
                return f"No external exercises found for category '{category_name}'."
            return "\n\n".join(formatted)
    except Exception as e:
        return f"Error fetching external exercises from wger API: {str(e)}"


def lookup_nutrition_data(food_item: str) -> str:
    """Fetches real-world nutritional breakdown (calories, protein, carbs, fat per 100g) for a food item or product from the Open Food Facts API.

    Args:
        food_item: Name of the food item or product to search for (e.g. "oatmeal", "greek yogurt", "banana", "chicken breast").

    Returns:
        A summary of nutritional data for matching products.
    """
    api_key = os.environ.get("NUTRITION_API_KEY", "")
    encoded_query = urllib.parse.quote(food_item)
    url = f"https://world.openfoodfacts.org/cgi/search.pl?search_terms={encoded_query}&search_simple=1&action=process&json=1&page_size=3"

    headers = {"User-Agent": "PulseCoachAI-FitnessAgent/1.0 (learning@example.com)"}
    if api_key:
        headers["X-Api-Key"] = api_key

    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            products = data.get("products", [])
            if not products:
                return f"No nutritional data found for '{food_item}'."

            results = []
            for p in products[:3]:
                name = p.get("product_name") or p.get("product_name_en") or food_item
                brands = p.get("brands", "Generic")
                nutriments = p.get("nutriments", {})

                cals = nutriments.get("energy-kcal_100g") or nutriments.get("energy-kcal_value") or "N/A"
                protein = nutriments.get("proteins_100g") or nutriments.get("proteins_value") or "N/A"
                carbs = nutriments.get("carbohydrates_100g") or nutriments.get("carbohydrates_value") or "N/A"
                fat = nutriments.get("fat_100g") or nutriments.get("fat_value") or "N/A"

                results.append(
                    f"🥗 {name} ({brands}):\n"
                    f"  - Calories: {cals} kcal / 100g\n"
                    f"  - Protein: {protein} g\n"
                    f"  - Carbs: {carbs} g\n"
                    f"  - Fat: {fat} g"
                )
            return "\n\n".join(results)
    except Exception as e:
        return f"Error fetching nutrition data for '{food_item}': {str(e)}"


def calculate_training_zones(
    age: int,
    resting_hr: int = 60,
    weight_kg: float = 70.0,
    height_cm: float = 175.0,
    gender: str = "unspecified",
) -> str:
    """Calculates personalized heart rate training zones, BMR, and recommended daily calorie targets.

    Args:
        age: Age of the user in years.
        resting_hr: Resting heart rate in beats per minute (bpm). Defaults to 60 bpm.
        weight_kg: Body weight in kilograms. Defaults to 70.0 kg.
        height_cm: Height in centimeters. Defaults to 175.0 cm.
        gender: Gender string ("male", "female", or "unspecified").

    Returns:
        A detailed summary of Max Heart Rate, Heart Rate Zones (Warmup, Fat Burn, Aerobic, Anaerobic, Peak), BMR, and Daily Maintenance Calories.
    """
    mhr = 220 - age
    hrr = mhr - resting_hr

    z1_low, z1_high = int(resting_hr + 0.50 * hrr), int(resting_hr + 0.60 * hrr)
    z2_low, z2_high = int(resting_hr + 0.60 * hrr), int(resting_hr + 0.70 * hrr)
    z3_low, z3_high = int(resting_hr + 0.70 * hrr), int(resting_hr + 0.80 * hrr)
    z4_low, z4_high = int(resting_hr + 0.80 * hrr), int(resting_hr + 0.90 * hrr)
    z5_low, z5_high = int(resting_hr + 0.90 * hrr), mhr

    # BMR calculation using Mifflin-St Jeor Formula
    if gender.lower() == "male":
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + 5
    elif gender.lower() == "female":
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age - 161
    else:
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age - 78

    tdee_sedentary = int(bmr * 1.2)
    tdee_moderate = int(bmr * 1.55)
    tdee_active = int(bmr * 1.725)

    return (
        f"📊 Training Metrics & HR Zones Summary (Age: {age}, Weight: {weight_kg}kg, Height: {height_cm}cm):\n\n"
        f"❤️ Heart Rate Metrics:\n"
        f"  - Max Heart Rate (MHR): {mhr} bpm\n"
        f"  - Resting Heart Rate: {resting_hr} bpm\n"
        f"  - Zone 1 (Very Light / Warmup, 50-60%): {z1_low} - {z1_high} bpm\n"
        f"  - Zone 2 (Light / Fat Burn, 60-70%): {z2_low} - {z2_high} bpm\n"
        f"  - Zone 3 (Moderate / Aerobic, 70-80%): {z3_low} - {z3_high} bpm\n"
        f"  - Zone 4 (Hard / Anaerobic Threshold, 80-90%): {z4_low} - {z4_high} bpm\n"
        f"  - Zone 5 (Maximum / Peak Effort, 90-100%): {z5_low} - {z5_high} bpm\n\n"
        f"🔥 Metabolic & Energy Targets:\n"
        f"  - Basal Metabolic Rate (BMR): {int(bmr)} kcal/day\n"
        f"  - Maintenance Calories (Sedentary): {tdee_sedentary} kcal/day\n"
        f"  - Maintenance Calories (Moderately Active): {tdee_moderate} kcal/day\n"
        f"  - Maintenance Calories (Vigorously Active): {tdee_active} kcal/day"
    )


def search_exercises(category: str = "", target_muscle: str = "") -> str:
    """Searches the exercises catalog in Firestore.

    Args:
        category: Optional filter by exercise category (e.g. Strength, Cardio, Core).
        target_muscle: Optional filter by target muscle group (e.g. Chest, Legs, Abs).

    Returns:
        A list of matching exercises with details.
    """
    db = firestore.Client(project=PROJECT_ID)
    collection_ref = db.collection("exercises")
    docs = collection_ref.stream()

    results = []
    for doc in docs:
        data = doc.to_dict()
        if category and category.lower() not in data.get("category", "").lower():
            continue
        if target_muscle and target_muscle.lower() not in data.get("target_muscle", "").lower():
            continue
        results.append(data)

    if not results:
        return "No matching exercises found in the catalog."

    formatted = []
    for ex in results:
        formatted.append(
            f"- {ex.get('name')} (Category: {ex.get('category')}, Target: {ex.get('target_muscle')}, "
            f"Difficulty: {ex.get('difficulty')}, Equipment: {ex.get('equipment')})\n  Description: {ex.get('description')}"
        )
    return "\n\n".join(formatted)


def log_workout(exercise_name: str, duration_minutes: int, calories_burned: int, notes: str = "") -> str:
    """Logs a completed workout session to Firestore.

    Args:
        exercise_name: Name of the exercise or workout performed.
        duration_minutes: Duration of the workout in minutes.
        calories_burned: Estimated calories burned.
        notes: Optional extra notes about the workout session.

    Returns:
        Confirmation message with the logged workout record.
    """
    db = firestore.Client(project=PROJECT_ID)
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()

    doc_data = {
        "exercise_name": exercise_name,
        "duration_minutes": duration_minutes,
        "calories_burned": calories_burned,
        "notes": notes,
        "timestamp": timestamp,
    }

    doc_ref = db.collection("workout_logs").document()
    doc_ref.set(doc_data)

    return (
        f"✓ Workout logged successfully!\n"
        f"  ID: {doc_ref.id}\n"
        f"  Exercise: {exercise_name}\n"
        f"  Duration: {duration_minutes} mins\n"
        f"  Calories Burned: {calories_burned} kcal\n"
        f"  Notes: {notes or 'N/A'}\n"
        f"  Timestamp: {timestamp}"
    )


def get_weather(query: str) -> str:
    """Simulates a web search. Use it get information on weather.

    Args:
        query: A string containing the location to get weather information for.

    Returns:
        A string with the simulated weather information for the queried location.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        return "It's 60 degrees and foggy."
    return "It's 90 degrees and sunny."


def get_current_time(query: str) -> str:
    """Simulates getting the current time for a city.

    Args:
        query: The name of the city to get the current time for.

    Returns:
        A string with the current time information.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        tz_identifier = "America/Los_Angeles"
    else:
        return f"Sorry, I don't have timezone information for query: {query}."

    tz = ZoneInfo(tz_identifier)
    now = datetime.datetime.now(tz)
    return f"The current time for query {query} is {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}"


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-2.5-flash",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    code_executor=code_executor,
    tools=[
        PreloadMemoryTool(),
        generate_workout_badge,
        geocode_address,
        find_nearby_places,
        fetch_external_exercises,
        lookup_nutrition_data,
        calculate_training_zones,
        search_exercises,
        log_workout,
        get_weather,
        get_current_time,
    ],
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
