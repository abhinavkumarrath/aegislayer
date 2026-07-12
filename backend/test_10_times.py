import requests
import json
import time
import uuid

API_URL = "http://127.0.0.1:8000/api/process"

TEST_CASES = [
    {
        "name": "Standard Mix",
        "prompt": "Hello John Smith (john@example.com). Please check 192.168.1.1. My API key is sk-12345678901234567890123456 and call +1 (555) 123-4567."
    },
    {
        "name": "Overlapping Same Entity Type",
        "prompt": "Dr. Sarah Jenkins and Dr. Michael Scott are leading the project. Send an email to sarah@acme.org and mike@acme.org."
    },
    {
        "name": "Complex Organization & Location",
        "prompt": "The meeting at Google Headquarters in Mountain View, California will be attended by Tim Cook from Apple."
    },
    {
        "name": "Punctuation Edge Cases",
        "prompt": "Contact Alice at alice@wonderland.com! Or bob.jones@example.co.uk. His IP: 10.0.0.1. Call +1-800-555-0199."
    },
    {
        "name": "Many Identical Entities",
        "prompt": "API keys are sk-aaaaabbbbbcccccdddddeeeeefffff and sk-111112222233333444445555566666. Also IP 8.8.8.8 and IP 8.8.4.4."
    },
    {
        "name": "Markdown Formatting",
        "prompt": "Dear **Elon Musk**, please review the following table:\n| Name | Email |\n|---|---|\n| Jeff Bezos | jeff@amazon.com |\n| Mark | mark@meta.com |"
    },
    {
        "name": "No Entities",
        "prompt": "Write a python script to calculate the fibonacci sequence efficiently."
    },
    {
        "name": "Credit Cards and Phones",
        "prompt": "My visa is 4532 1234 5678 9012. Call me at +44 20 7946 0958 or 555-867-5309."
    },
    {
        "name": "Line Breaks and Whitespace",
        "prompt": "Hello\n\nAbhinav\n\n\nHere is my email: \t abhi@ggits.edu \n\nThanks,   GGITS"
    },
    {
        "name": "Stress Test Maximum Entities",
        "prompt": "A B C D E F G H I J K L M N O P Q R S T U V W X Y Z. Emails: a@a.com b@b.com c@c.com d@d.com e@e.com. IPs: 1.1.1.1 2.2.2.2 3.3.3.3 4.4.4.4."
    }
]

def run_tests():
    print("Starting 10x Rigorous AegisLayer Pipeline Tests...")
    print("="*60)
    
    success_count = 0
    total_time = 0
    
    session_id = str(uuid.uuid4())
    
    for i, test in enumerate(TEST_CASES):
        print(f"Test {i+1}/10: {test['name']}")
        start_time = time.time()
        
        payload = {
            "session_id": session_id,
            "prompt": test["prompt"],
            "model": "llama-3.1-8b-instant" # Fast free model
        }
        
        try:
            response = requests.post(API_URL, json=payload, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            latency = time.time() - start_time
            total_time += latency
            
            # Basic validation
            assert "sanitized_prompt" in data
            assert "final_de_sanitized_response" in data
            assert "audit_logs" in data
            
            print(f"  [SUCCESS] Latency: {latency:.2f}s | Redacted: {len(data['audit_logs'])}")
            print(f"  Input:    {test['prompt'][:60]}...")
            print(f"  Border:   {data['sanitized_prompt'][:60]}...")
            print(f"  Output:   {data['final_de_sanitized_response'][:60].replace(chr(10), ' ')}...")
            
            success_count += 1
            
        except Exception as e:
            print(f"  [FAILED] Error: {e}")
            if 'response' in locals() and response is not None:
                print(f"  Response Body: {response.text}")
                
        print("-" * 60)
        time.sleep(1) # Small delay to not rate limit the LLM
        
    print(f"\nTest Summary: {success_count}/10 Passed")
    print(f"Average Latency: {total_time/len(TEST_CASES):.2f}s")
    
if __name__ == "__main__":
    run_tests()
