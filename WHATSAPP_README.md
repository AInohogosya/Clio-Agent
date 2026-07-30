# WhatsApp Integration for Clio-Agent-2

Comprehensive WhatsApp Business API integration for sending and receiving prompts via WhatsApp using the official WhatsApp Cloud API (via pywa) or the Baileys library.

## Features

### Full Bot Mode (Recommended - WhatsApp Business API)
- **Two-way messaging**: Receive and respond to messages via webhook
- **Multi-format support**: Handle text, images, videos, audio, documents, stickers, locations, and contacts
- **Interactive messages**: Send buttons and list messages for better UX
- **Session management**: Track user sessions and conversation history
- **Rate limiting**: Built-in protection against spam (20 messages/minute per user)
- **Markdown formatting**: Support for bold, italic, strikethrough, and code blocks
- **Long message handling**: Automatic splitting of messages over 4096 characters
- **Broadcast capability**: Send notifications to all known users (autonomous mode)
- **Error handling**: Graceful error recovery and retry logic

### Simple Message Mode (Baileys Library)
- **Quick message sending**: Send one-off messages without full bot setup
- **QR code authentication**: Connect via QR code scanning
- **Session persistence**: No need to scan QR every time
- **Auto-reconnection**: Automatically reconnect on disconnection

## Setup Options

### Option 1: WhatsApp Business API (Production Recommended)

#### Prerequisites
1. **Meta Developer Account**: Create at https://developers.facebook.com/
2. **WhatsApp Business Account**: Link to your Meta Developer account
3. **Phone Number**: Dedicated phone number for WhatsApp Business
4. **Webhook URL**: Public URL for receiving messages (use ngrok for local development)

#### Configuration Steps

1. **Create Meta Developer App**
   ```bash
   # Visit https://developers.facebook.com/
   # Create new app → Add WhatsApp product
   ```

2. **Get Required Credentials**
   - Phone Number ID (from WhatsApp Business Account)
   - Access Token (from Developer Dashboard)
   - App Secret (from Developer Dashboard)
   - Webhook Verify Token (create your own)

3. **Set Up Webhook**
   ```bash
   # For local development, use ngrok
   ngrok http 8080
   
   # Copy the HTTPS URL (e.g., https://abc123.ngrok.io)
   # This becomes your WHATSAPP_WEBHOOK_URL
   ```

4. **Configure Environment Variables**
   ```bash
   export WHATSAPP_PHONE_NUMBER_ID="your_phone_number_id"
   export WHATSAPP_ACCESS_TOKEN="your_access_token"
   export WHATSAPP_APP_SECRET="your_app_secret"
   export WHATSAPP_WEBHOOK_VERIFY_TOKEN="your_verify_token"
   export WHATSAPP_WEBHOOK_URL="https://your-domain.com"
   export WHATSAPP_WEBHOOK_PORT=8080
   ```

5. **Install Python Dependencies**
   ```bash
   pip install pywa
   ```

6. **Run the Bot**
   ```bash
   python3 run.py --whatsapp
   ```

### Option 2: Baileys Library (Quick Testing)

#### Installation
```bash
npm install
```

#### Usage

**As a Module:**
```javascript
const WhatsAppService = require('./whatsapp_service');

const whatsapp = new WhatsAppService();
await whatsapp.connect();
await whatsapp.sendMessage('1234567890', 'Hello World!');
await whatsapp.disconnect();
```

**As a Standalone Script:**
```bash
# Connect and send message
node whatsapp_service.js 1234567890 "Hello from Baileys!"

# Just connect (for testing)
node whatsapp_service.js
```

**From Python:**
```python
from interfaces.whatsapp import send_whatsapp_message

await send_whatsapp_message("1234567890", "Your message here")
```

## API Reference

### WhatsAppInterface Class

#### Constructor
```python
interface = WhatsAppInterface(
    agent=ClioAgent,                    # Agent instance
    phone_number_id="your_id",          # WhatsApp Business Phone Number ID
    access_token="your_token",          # API Access Token
    app_secret="your_secret",           # Meta App Secret
    webhook_verify_token="verify",      # Webhook verification token
    webhook_url="https://domain.com",   # Public webhook URL
    port=8080                           # Local server port
)
```

#### Methods

**start()**
```python
await interface.start()
```
Starts the WhatsApp bot and begins listening for messages.

**stop()**
```python
await interface.stop()
```
Gracefully stops the bot.

**send_broadcast(message, exclude_users)**
```python
count = await interface.send_broadcast(
    message="Autonomous mode notification",
    exclude_users=["user1@s.whatsapp.net"]
)
```
Broadcasts a message to all known users. Returns count of sent messages.

**send_interactive_message(recipient, text, buttons, footer)**
```python
success = await interface.send_interactive_message(
    recipient="1234567890@s.whatsapp.net",
    text="Choose an option:",
    buttons=[
        {"id": "btn1", "title": "Option 1"},
        {"id": "btn2", "title": "Option 2"}
    ],
    footer="Please select"
)
```
Sends an interactive message with up to 3 buttons.

**get_session_info(user_id)**
```python
info = interface.get_session_info("1234567890@s.whatsapp.net")
# Returns: {"name": "User Name", "last_active": timestamp, "message_count": 5}
```

**get_all_sessions()**
```python
sessions = interface.get_all_sessions()
# Returns dict of all active user sessions
```

### WhatsAppService Class (Baileys)

#### Methods

**connect()**
```python
await service.connect()
```
Connects to WhatsApp Web via QR code.

**sendMessage(phoneNumber, message)**
```python
await service.sendMessage("1234567890", "Hello!")
```
Sends a simple text message.

**sendPrompt(phoneNumber, prompt, options)**
```python
await service.sendPrompt("1234567890", "Your prompt", {
    "title": "Prompt Title",
    "footer": "Footer text",
    "buttons": [
        {"id": "btn1", "text": "Option 1"}
    ]
})
```
Sends a formatted prompt with optional buttons.

**disconnect()**
```python
await service.disconnect()
```
Disconnects from WhatsApp Web.

**getStatus()**
```python
status = service.getStatus()
# Returns: {"connected": True, "socket": "initialized"}
```

## Message Type Handlers

The enhanced WhatsApp interface supports multiple message types:

| Type | Handler | Description |
|------|---------|-------------|
| Text | `_handle_text_message` | Process text messages and captions |
| Image | `_handle_image_message` | Handle image messages with optional caption |
| Video | `_handle_video_message` | Handle video messages with optional caption |
| Audio | `_handle_audio_message` | Handle audio/voice messages |
| Document | `_handle_document_message` | Handle document attachments |
| Sticker | `_handle_sticker_message` | Handle sticker messages |
| Location | `_handle_location_message` | Handle location shares |
| Contact | `_handle_contact_message` | Handle contact card shares |

## Rate Limiting

Built-in rate limiting prevents abuse:
- **Limit**: 20 messages per minute per user
- **Window**: Rolling 60-second window
- **Response**: Friendly warning when limit exceeded

## Character Limits

| Type | Limit |
|------|-------|
| Text Message | 4096 characters |
| Caption | 1024 characters |
| Buttons | 3 maximum |
| List Rows | 10 per section |

## Markdown Support

WhatsApp supports these markdown formats:
- `*bold*` → **bold**
- `_italic_` → *italic*
- `~strikethrough~` → ~strikethrough~
- \`\`\`code\`\`\` → monospace code block

The `sanitize_whatsapp_markdown()` function automatically fixes unclosed formatting tags.

## Error Handling

The interface handles common errors gracefully:
- Connection timeouts
- Rate limiting from WhatsApp
- Invalid message formats
- Network interruptions
- Agent processing timeouts

## Troubleshooting

### "pywa library not installed"
```bash
pip install pywa
```

### "Webhook URL is required"
Set up a public URL using ngrok or deploy to a server:
```bash
ngrok http 8080
export WHATSAPP_WEBHOOK_URL="https://abc123.ngrok.io"
```

### "Configuration incomplete"
Check that all required environment variables are set:
```bash
python3 run.py status
```

### QR Code Not Appearing (Baileys)
Delete the session folder and reconnect:
```bash
rm -rf whatsapp_session/
node whatsapp_service.js
```

### Messages Not Received
1. Check webhook URL is publicly accessible
2. Verify webhook is registered in Meta Developer Dashboard
3. Check server logs for incoming webhook requests
4. Ensure port 8080 is open

## Best Practices

1. **Use Business API for production**: More reliable and officially supported
2. **Secure your tokens**: Never commit credentials to version control
3. **Use webhooks efficiently**: Respond within 25 seconds to avoid retries
4. **Handle long messages**: Split messages over 4096 characters
5. **Test with small groups**: Before broadcasting to all users
6. **Monitor rate limits**: Implement backoff strategies
7. **Log important events**: Track message delivery and errors

## License

ISC
