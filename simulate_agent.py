import requests
from bs4 import BeautifulSoup
import json
import re

BASE_URL = "http://localhost:3000"
API_URL = "http://localhost:8000"

def simulate_agent_flow():
    print("🤖 [Agent] Booting up... Target: AgentHub.OS")
    
    # 1. Discovery (Handshake)
    print(f"\n👀 [Agent] Visiting Homepage: {BASE_URL}")
    try:
        response = requests.get(BASE_URL)
        response.raise_for_status()
    except Exception as e:
        print(f"❌ Could not connect to {BASE_URL}. Is frontend running?")
        return

    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Look for the "Agent Instructions" tag
    instruction_link = soup.find("link", {"rel": "alternate", "type": "text/markdown"})
    
    if instruction_link:
        path = instruction_link.get("href")
        print(f"✅ [Agent] Discovery Successful! Found instructions at: {path}")
        
        # 2. Learning
        instruction_url = f"{BASE_URL}{path}"
        print(f"📖 [Agent] Reading Manual: {instruction_url} ...")
        manual_res = requests.get(instruction_url)
        if manual_res.status_code == 200:
            print(f"   [Manual Content Snippet]:\n   {manual_res.text[:100]}...\n")
        else:
            print(f"❌ 404 Error Reading Manual at {instruction_url}")
            # Try fallback to API Gateway directly just to show it works
            print(f"   (Fallback: Trying backend directly at {API_URL}/agent.md)")
            manual_res = requests.get(f"{API_URL}/agent.md")
            if manual_res.status_code == 200:
                 print("   ✅ Found it on Backend!")
            else:
                 return

        # 3. Action (Create Repo)
        print("\n🛠️  [Agent] Decided to Create a Project...")
        repo_name = f"auto-agent-{json.dumps(str(hash(response.text)))[-6:-1]}.git"
        
        payload = {"name": repo_name}
        print(f"   POST {API_URL}/repos | Payload: {payload}")
        
        try:
            create_res = requests.post(f"{API_URL}/repos", json=payload)
            if create_res.status_code == 200:
                print(f"✅ [Agent] Success! Repo Created: {create_res.json()}")
            else:
                print(f"❌ Failed: {create_res.text}")
        except Exception as e:
             print(f"❌ Connection Error to Backend: {e}")

    else:
        print("❌ [Agent] Failed to find instruction link in HTML head.")

if __name__ == "__main__":
    simulate_agent_flow()
