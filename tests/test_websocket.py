"""Test WebSocket connection and flow."""
import requests
import json
import asyncio
import websockets
import time

async def test_websocket():
    # Create session
    print('Creating session...')
    r = requests.post('http://127.0.0.1:8000/api/v1/sessions', json={'client_id': 'test_cli'})
    print(f'Response: {r.status_code}')
    data = r.json()
    session_id = data['session_id']
    print(f'Session: {session_id}')
    
    # Connect WebSocket
    ws_url = f'ws://127.0.0.1:8000/api/v1/ws/{session_id}'
    print(f'Connecting to: {ws_url}')
    
    try:
        async with websockets.connect(ws_url, ping_interval=None) as ws:
            print('[WS CONNECTED]')
            
            # Listen for messages for 15 seconds
            start = time.time()
            messages = []
            
            while time.time() - start < 15:
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    msg = json.loads(message)
                    msg_type = msg.get('type', 'unknown')
                    
                    print(f'[MSG] Type: {msg_type}')
                    
                    if msg_type == 'agent_speech':
                        text = msg.get('text', '')[:100]
                        audio_len = len(msg.get('audio', ''))
                        print(f'       Text: {text}...')
                        print(f'       Audio: {audio_len} chars')
                    elif msg_type == 'conversation_status':
                        print(f'       State: {msg.get("agent_state")}')
                        print(f'       Can speak: {msg.get("can_speak")}')
                    elif msg_type == 'error':
                        print(f'       ERROR: {msg.get("message_en")}')
                    else:
                        print(f'       Data: {str(msg)[:200]}')
                    
                    messages.append(msg)
                    
                except asyncio.TimeoutError:
                    print('.', end='', flush=True)
                except Exception as e:
                    print(f'[ERROR] {e}')
                    break
            
            print(f'\n\nReceived {len(messages)} messages')
            print('\nMessage types received:')
            for msg in messages:
                print(f'  - {msg.get("type")}')
                
    except Exception as e:
        print(f'[CONNECTION ERROR] {e}')

if __name__ == '__main__':
    asyncio.run(test_websocket())
