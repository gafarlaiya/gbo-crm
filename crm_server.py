from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import os
import requests
import sqlite3
import os

app = Flask(__name__)
CORS(app) # Enable CORS for frontend connection

# Use absolute path for SQLite so Gunicorn finds it reliably in cloud
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "crm_database.db")

def init_db():
    conn = sqlite3.connect(DB_FILE, timeout=10)
    c = conn.cursor()
    # Create Leads Table
    c.execute('''
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL UNIQUE,
            crop_type TEXT,
            plant_count TEXT,
            plant_age TEXT,
            challenge TEXT,
            source TEXT,
            status TEXT DEFAULT 'New',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create Messages Table for Omnichannel Inbox
    c.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            channel TEXT DEFAULT 'WhatsApp',
            direction TEXT, -- 'inbound' or 'outbound'
            content TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(lead_id) REFERENCES leads(id)
        )
    ''')
    conn.commit()
    conn.close()

# Initialize DB on load so Render always creates it
init_db()

# WhatsApp Cloud API Settings (Live Configuration)
VERIFY_TOKEN = "GBO_CRM_SECURE_TOKEN"
WHATSAPP_TOKEN = "EAAXvuLkDScEBQ7y0Nbf6EqyqpARdi3ohBumXtutZBummZBvFaJG305ZBsGEtT9ucFXEbZCCr7nU2HY8sf5kB8K95Hey2Cmyo5ZCCjkHGYQjVmttZAoVaUtiPcEZBope7EOEjA2MrhLPdRgLzfIz2uNox0zdURP2CjTCVmM6grDZA3yLjZAFsukujMJeZA4OwQe1ZBDWbXCd5rDMMvKrkCMOOA7ipSKam8RDAyFktWYOZCzUD39DJWZCZC81MrZCnL3m2AU1fQW6guO1knIXo3rOCr6zHPaQZCQZDZD"
WHATSAPP_PHONE_ID = "514296761762563"

@app.route('/webhook', methods=['GET'])
def verify_webhook():
    """
    Meta requires a GET request to verify the webhook URL during setup.
    """
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')

    if mode and token:
        if mode == 'subscribe' and token == VERIFY_TOKEN:
            print("WEBHOOK_VERIFIED")
            return challenge, 200
        else:
            return 'Forbidden', 403
    return 'Bad Request', 400

@app.route('/webhook', methods=['POST'])
def receive_message():
    """
    Receive incoming messages and status updates from WhatsApp.
    """
    body = request.json
    
    if body.get('object'):
        if body.get('entry') and body['entry'][0].get('changes') and body['entry'][0]['changes'][0].get('value').get('messages'):
            # Extract important data
            msg_data = body['entry'][0]['changes'][0]['value']['messages'][0]
            phone_number = msg_data['from']
            msg_text = msg_data['text']['body']
            
            print(f"New WhatsApp Message from {phone_number}: {msg_text}")
            
            # Here we would look up the lead by phone_number and insert into the messages table
            try:
                conn = sqlite3.connect(DB_FILE, timeout=10)
                c = conn.cursor()
                
                # Try to find existing lead
                c.execute("SELECT id FROM leads WHERE phone = ?", (phone_number,))
                lead = c.fetchone()
                
                if lead:
                    lead_id = lead[0]
                else:
                    # Create a generic new lead if they aren't in the system yet
                    c.execute("INSERT INTO leads (name, phone, source) VALUES (?, ?, ?)", ("Unknown WhatsApp Contact", phone_number, "WhatsApp Inbound"))
                    lead_id = c.lastrowid
                    
                # Store the message
                c.execute("INSERT INTO messages (lead_id, channel, direction, content) VALUES (?, ?, ?, ?)", (lead_id, 'WhatsApp', 'inbound', msg_text))
                conn.commit()
                conn.close()
                print("Message stored successfully linked to lead.")
            except Exception as e:
                print(f"Error saving message: {e}")

        return jsonify({"status": "ok"}), 200
    else:
        return 'Not Found', 404

@app.route('/api/leads', methods=['POST'])
def add_lead():
    data = request.json
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10)
        c = conn.cursor()
        c.execute('''
            INSERT INTO leads (name, phone, crop_type, plant_count, plant_age, challenge, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            data.get('name'), 
            data.get('phone'), 
            data.get('cropType'), 
            data.get('plantCount'), 
            data.get('plantAge'), 
            data.get('challenge'), 
            data.get('source')
        ))
        conn.commit()
        lead_id = c.lastrowid
        conn.close()
        return jsonify({"success": True, "message": "Lead captured successfully", "lead_id": lead_id}), 201
    except sqlite3.IntegrityError:
        return jsonify({"success": False, "message": "A lead with this phone number already exists."}), 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/leads', methods=['GET'])
def get_leads():
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM leads ORDER BY created_at DESC")
        leads = [dict(row) for row in c.fetchall()]
        conn.close()
        return jsonify(leads), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/messages/<int:lead_id>', methods=['GET'])
def get_messages(lead_id):
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM messages WHERE lead_id = ? ORDER BY timestamp ASC", (lead_id,))
        messages = [dict(row) for row in c.fetchall()]
        conn.close()
        return jsonify(messages), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/messages', methods=['POST'])
def send_message():
    data = request.json
    lead_id = data.get('lead_id')
    content = data.get('content')
    channel = data.get('channel', 'WhatsApp')
    
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        # 1. Fetch Lead's phone number
        c.execute("SELECT phone FROM leads WHERE id = ?", (lead_id,))
        lead = c.fetchone()
        
        if not lead:
            return jsonify({"error": "Lead not found"}), 404
            
        phone_raw = lead['phone']
        # Clean phone number (remove non-digits). Essential for WhatsApp API.
        phone = ''.join(filter(str.isdigit, phone_raw))
        
        # 2. Call WhatsApp Cloud API
        if channel == 'WhatsApp' and WHATSAPP_TOKEN and WHATSAPP_PHONE_ID:
            url = f"https://graph.facebook.com/v19.0/{WHATSAPP_PHONE_ID}/messages"
            headers = {
                "Authorization": f"Bearer {WHATSAPP_TOKEN}",
                "Content-Type": "application/json"
            }
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "text",
                "text": {"body": content}
            }
            
            # Send to Meta
            meta_resp = requests.post(url, headers=headers, json=payload)
            if meta_resp.status_code not in [200, 201]:
                print(f"WhatsApp API Error: {meta_resp.text}")
                # We log it, but we still want to save the message locally below so 
                # the user sees what they tried to send
        
        # 3. Save to database
        c.execute("INSERT INTO messages (lead_id, channel, direction, content) VALUES (?, ?, 'outbound', ?)", 
                  (lead_id, channel, content))
        conn.commit()
        msg_id = c.lastrowid
        conn.close()
        return jsonify({"success": True, "message_id": msg_id}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    print("Initializing Database...")
    init_db()
    print("Starting CRM Backend Server on port 5000...")
    app.run(port=5000, debug=True)
