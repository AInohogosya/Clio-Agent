const { default: makeWASocket, useMultiFileAuthState, DisconnectReason } = require('@whiskeysockets/baileys');
const qrcode = require('qrcode-terminal');
const fs = require('fs');
const path = require('path');

// Directory to store session authentication files
const AUTH_DIR = path.join(__dirname, 'whatsapp_session');

/**
 * WhatsApp Service class for sending prompts via WhatsApp Web using Baileys
 */
class WhatsAppService {
    constructor() {
        this.socket = null;
        this.isConnected = false;
    }

    /**
     * Initialize and connect to WhatsApp Web
     */
    async connect() {
        // Ensure auth directory exists
        if (!fs.existsSync(AUTH_DIR)) {
            fs.mkdirSync(AUTH_DIR, { recursive: true });
        }

        const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);

        this.socket = makeWASocket({
            auth: state,
            printQRInTerminal: false, // We'll handle QR code display manually
        });

        // Handle connection updates
        this.socket.ev.on('connection.update', async (update) => {
            const { connection, lastDisconnect, qr } = update;

            if (qr) {
                // Display QR code for scanning
                console.log('\n=== WhatsApp Web QR Code ===');
                qrcode.generate(qr, { small: true });
                console.log('Scan the QR code with WhatsApp on your phone to connect.\n');
            }

            if (connection === 'close') {
                const shouldReconnect = 
                    lastDisconnect.error?.output?.statusCode !== DisconnectReason.loggedOut;
                
                console.log('Connection closed. Reconnecting:', shouldReconnect);
                this.isConnected = false;

                if (shouldReconnect) {
                    await this.connect();
                }
            } else if (connection === 'open') {
                console.log('✅ Successfully connected to WhatsApp Web!');
                this.isConnected = true;
            }
        });

        // Handle credentials update
        this.socket.ev.on('creds.update', saveCreds);

        // Wait for connection to be established
        return new Promise((resolve, reject) => {
            const checkConnection = setInterval(() => {
                if (this.isConnected) {
                    clearInterval(checkConnection);
                    resolve(this.socket);
                }
            }, 500);

            // Timeout after 60 seconds
            setTimeout(() => {
                clearInterval(checkConnection);
                if (!this.isConnected) {
                    reject(new Error('Connection timeout - please try again'));
                }
            }, 60000);
        });
    }

    /**
     * Send a text message/prompt to a WhatsApp number
     * @param {string} phoneNumber - The recipient's phone number (with country code, e.g., '1234567890')
     * @param {string} message - The message/prompt to send
     * @returns {Promise<object>} - The send result
     */
    async sendMessage(phoneNumber, message) {
        if (!this.isConnected || !this.socket) {
            throw new Error('Not connected to WhatsApp. Please call connect() first.');
        }

        // Format phone number: remove any non-digit characters except +
        const cleanNumber = phoneNumber.replace(/\D/g, '');
        
        // Add @s.whatsapp.net suffix for individual chats
        const jid = `${cleanNumber}@s.whatsapp.net`;

        console.log(`Sending message to ${phoneNumber}...`);

        const result = await this.socket.sendMessage(jid, {
            text: message
        });

        console.log('Message sent successfully!');
        return result;
    }

    /**
     * Send a prompt with additional options (markdown, buttons, etc.)
     * @param {string} phoneNumber - The recipient's phone number
     * @param {string} prompt - The prompt text
     * @param {object} options - Additional options
     * @returns {Promise<object>}
     */
    async sendPrompt(phoneNumber, prompt, options = {}) {
        const { 
            footer = '', 
            buttons = [], 
            title = '' 
        } = options;

        if (buttons.length > 0) {
            // Send interactive message with buttons
            const jid = `${phoneNumber.replace(/\D/g, '')}@s.whatsapp.net`;
            
            const result = await this.socket.sendMessage(jid, {
                text: `${title ? `*${title}*\n\n` : ''}${prompt}\n\n${footer}`,
                buttons: buttons.map(btn => ({
                    buttonId: btn.id,
                    buttonText: { displayText: btn.text }
                })),
                headerType: 1
            });

            return result;
        } else {
            // Simple text message
            return await this.sendMessage(phoneNumber, prompt);
        }
    }

    /**
     * Disconnect from WhatsApp Web
     */
    async disconnect() {
        if (this.socket) {
            await this.socket.end(undefined);
            this.isConnected = false;
            console.log('Disconnected from WhatsApp Web');
        }
    }

    /**
     * Get connection status
     */
    getStatus() {
        return {
            connected: this.isConnected,
            socket: this.socket ? 'initialized' : 'not initialized'
        };
    }
}

// Export the service
module.exports = WhatsAppService;

// Example usage when run directly
if (require.main === module) {
    const whatsappService = new WhatsAppService();

    // Example: Run as standalone script
    (async () => {
        try {
            console.log('Initializing WhatsApp connection...\n');
            await whatsappService.connect();

            // Example: Send a test message
            // Replace with actual phone number for testing
            const testNumber = process.argv[2];
            const testMessage = process.argv[3] || 'Hello! This is a test message from Baileys.';

            if (testNumber) {
                console.log(`\nSending test message to ${testNumber}...`);
                await whatsappService.sendPrompt(testNumber, testMessage, {
                    title: 'Test Prompt',
                    footer: 'Powered by Baileys'
                });
            } else {
                console.log('\n✅ Connected! Usage: node whatsapp_service.js <phone_number> [message]');
                console.log('Example: node whatsapp_service.js 1234567890 "Hello World"');
            }

            // Keep the process running
            console.log('\nPress Ctrl+C to disconnect and exit.');
        } catch (error) {
            console.error('Error:', error.message);
            process.exit(1);
        }
    })();
}
