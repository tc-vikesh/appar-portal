import requests
import json

try:
    print("Hitting diagnostic route...")
    r = requests.get('http://127.0.0.1:8000/portal/test-m2p-crypto/')
    data = r.json()
    
    with open('d:/Apaar/Dev/codebase/tap_project/diag_results.json', 'w') as f:
        json.dump(data, f, indent=4)
        
    print("Results saved to diag_results.json")
    
    # Analyze
    live_results = data.get("live_results", [])
    print(f"Total test cases run: {len(live_results)}")
    
    successes = [res for res in live_results if res.get('http_status') == 200]
    print(f"Total successes (200 OK): {len(successes)}")
    for s in successes:
        print(f"SUCCESS: {s['test_case']} -> {s['raw_response']}")
        
    # If no successes, show unique status codes and some errors
    if not successes:
        statuses = {}
        for res in live_results:
            status = res.get('http_status')
            statuses[status] = statuses.get(status, 0) + 1
        print("Status code distribution:", statuses)
        
        # Show one sample error for each unique URL
        shown_urls = set()
        for res in live_results:
            test_case = res.get('test_case', '')
            url = test_case.split('|')[1].strip() if '|' in test_case else test_case
            if url not in shown_urls:
                shown_urls.add(url)
                print(f"Sample response for {test_case}:")
                print(f"  HTTP {res.get('http_status')}: {res.get('raw_response')}")
                if res.get('decryption_error'):
                    print(f"  Decryption Error: {res.get('decryption_error')}")
except Exception as e:
    print("Error scanning results:", e)
