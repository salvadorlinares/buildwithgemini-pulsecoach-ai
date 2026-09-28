import asyncio
import os
from playwright.async_api import async_playwright

VIDEO_DIR = "/config/.gemini/antigravity/brain/e2828c5e-f93d-4512-8f19-da4ec1ccad4b/video_output"
os.makedirs(VIDEO_DIR, exist_ok=True)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            record_video_dir=VIDEO_DIR,
            record_video_size={"width": 1280, "height": 800}
        )
        page = await context.new_page()
        
        print("Navigating to http://localhost:8080...")
        await page.goto("http://localhost:8080")
        await page.wait_for_timeout(3000)
        
        # Prompt 1: Core app feature
        prompt1 = "Hi PulseCoach! Can you calculate my heart rate training zones and daily BMR? I am 30 years old, 75kg, 178cm, resting HR 62."
        print(f"Sending Prompt 1: {prompt1}")
        
        input_elem = await page.query_selector("input, textarea")
        if input_elem:
            await input_elem.fill(prompt1)
            await page.wait_for_timeout(1000)
            send_btn = await page.query_selector("button[type='submit'], #send-btn, button")
            if send_btn:
                await send_btn.click()
            else:
                await page.keyboard.press("Enter")
        
        print("Waiting for response 1...")
        await page.wait_for_timeout(15000)
        
        # Prompt 2: Richer prompt showing tool calls, database lookup & image generation
        prompt2 = "Search strength exercises for chest in my database, find nearby gyms in San Francisco, and generate a '10K Finisher' workout badge!"
        print(f"Sending Prompt 2: {prompt2}")
        
        input_elem = await page.query_selector("input, textarea")
        if input_elem:
            await input_elem.fill(prompt2)
            await page.wait_for_timeout(1000)
            send_btn = await page.query_selector("button[type='submit'], #send-btn, button")
            if send_btn:
                await send_btn.click()
            else:
                await page.keyboard.press("Enter")
        
        print("Waiting for response 2...")
        await page.wait_for_timeout(25000)
        
        await page.wait_for_timeout(5000)
        
        video_path = await page.video.path()
        print(f"Video saved at: {video_path}")
        
        await context.close()
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
