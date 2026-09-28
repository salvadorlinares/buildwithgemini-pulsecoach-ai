"""
Seed script to populate Firestore with initial exercise data.
"""

from google.cloud import firestore

PROJECT_ID = "qwiklabs-gcp-01-2d29658a1c8c"

EXERCISES = [
    {
        "id": "pushups",
        "name": "Push-Ups",
        "category": "Strength",
        "target_muscle": "Chest & Triceps",
        "difficulty": "Beginner",
        "equipment": "Bodyweight",
        "description": "Place hands shoulder-width apart, keep core tight, and lower chest to floor.",
    },
    {
        "id": "barbell_squat",
        "name": "Barbell Squat",
        "category": "Strength",
        "target_muscle": "Quadriceps & Glutes",
        "difficulty": "Intermediate",
        "equipment": "Barbell",
        "description": "Rest bar on upper back, bend knees while driving hips back, and stand back up.",
    },
    {
        "id": "hiit_sprints",
        "name": "HIIT Running Intervals",
        "category": "Cardio",
        "target_muscle": "Cardiovascular & Legs",
        "difficulty": "Advanced",
        "equipment": "None",
        "description": "Alternate 30 seconds of max effort sprint with 60 seconds of light jogging for 15-20 minutes.",
    },
    {
        "id": "plank",
        "name": "Core Plank",
        "category": "Core",
        "target_muscle": "Abs & Lower Back",
        "difficulty": "Beginner",
        "equipment": "Bodyweight",
        "description": "Hold body straight on forearms and toes while contracting abdominals for 60 seconds.",
    },
    {
        "id": "dumbbell_row",
        "name": "Single-Arm Dumbbell Row",
        "category": "Strength",
        "target_muscle": "Upper Back & Biceps",
        "difficulty": "Intermediate",
        "equipment": "Dumbbell",
        "description": "Place knee and hand on bench, pull dumbbell towards hip maintaining neutral spine.",
    },
]

def seed():
    db = firestore.Client(project=PROJECT_ID)
    collection_ref = db.collection("exercises")
    
    print(f"Seeding Firestore collection 'exercises' in project '{PROJECT_ID}'...")
    for item in EXERCISES:
        doc_ref = collection_ref.document(item["id"])
        doc_ref.set(item)
        print(f"  ✓ Added exercise: {item['name']} ({item['id']})")
    
    print("Seeding complete!")

if __name__ == "__main__":
    seed()
