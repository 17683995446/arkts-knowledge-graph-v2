/**
 * EncryptionUtility.ets
 * Encryption and security utilities
 */

import crypto from '@ohos.crypto';

/**
 * Encryption utility for password hashing and JWT operations
 */
export class EncryptionUtility {
    private jwtSecret: string;
    private readonly SALT_ROUNDS = 12;
    
    constructor(jwtSecret: string) {
        this.jwtSecret = jwtSecret;
    }
    
    /**
     * Hash password using bcrypt-like algorithm
     */
    async hash(password: string): Promise<string> {
        const salt = await this.generateSalt();
        const hash = await this.pbkdf2(password, salt, 10000);
        return `${salt}$${hash}`;
    }
    
    /**
     * Compare password with hash
     */
    async compare(password: string, hash: string): Promise<boolean> {
        const [salt, originalHash] = hash.split('$');
        if (!salt || !originalHash) return false;
        
        const computedHash = await this.pbkdf2(password, salt, 10000);
        return this.timingSafeEqual(originalHash, computedHash);
    }
    
    /**
     * Generate random ID
     */
    generateRandomId(): string {
        return crypto.randomUUID();
    }
    
    /**
     * Sign JWT token
     */
    async signJWT(payload: object): Promise<string> {
        const header = { alg: 'HS256', typ: 'JWT' };
        const encodedHeader = this.base64UrlEncode(JSON.stringify(header));
        const encodedPayload = this.base64UrlEncode(JSON.stringify(payload));
        
        const signature = await this.hmacSHA256(
            `${encodedHeader}.${encodedPayload}`,
            this.jwtSecret
        );
        
        return `${encodedHeader}.${encodedPayload}.${signature}`;
    }
    
    /**
     * Verify JWT token
     */
    async verifyJWT(token: string): Promise<any> {
        const [header, payload, signature] = token.split('.');
        if (!header || !payload || !signature) {
            throw new Error('Invalid token format');
        }
        
        const expectedSignature = await this.hmacSHA256(
            `${header}.${payload}`,
            this.jwtSecret
        );
        
        if (!this.timingSafeEqual(signature, expectedSignature)) {
            throw new Error('Invalid signature');
        }
        
        return JSON.parse(this.base64UrlDecode(payload));
    }
    
    /**
     * Decode JWT without verification
     */
    decodeJWT(token: string): any {
        const parts = token.split('.');
        if (parts.length !== 3) {
            throw new Error('Invalid token format');
        }
        
        return JSON.parse(this.base64UrlDecode(parts[1]));
    }
    
    /**
     * Generate secure random string
     */
    generateSecureToken(length: number = 32): string {
        const bytes = crypto.randomBytes(length);
        return this.base64UrlEncode(bytes);
    }
    
    /**
     * Encrypt data with AES
     */
    async encrypt(data: string, key: string): Promise<string> {
        const iv = crypto.randomBytes(16);
        const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
        let encrypted = cipher.update(data, 'utf8', 'base64');
        encrypted += cipher.final('base64');
        const authTag = cipher.getAuthTag();
        return `${iv.toString('base64')}$${authTag.toString('base64')}$${encrypted}`;
    }
    
    /**
     * Decrypt AES encrypted data
     */
    async decrypt(encryptedData: string, key: string): Promise<string> {
        const [ivBase64, authTagBase64, encrypted] = encryptedData.split('$');
        const iv = Buffer.from(ivBase64, 'base64');
        const authTag = Buffer.from(authTagBase64, 'base64');
        
        const decipher = crypto.createDecipheriv('aes-256-gcm', key, iv);
        decipher.setAuthTag(authTag);
        
        let decrypted = decipher.update(encrypted, 'base64', 'utf8');
        decrypted += decipher.final('utf8');
        return decrypted;
    }
    
    // Private helper methods
    
    private async generateSalt(): Promise<string> {
        const bytes = crypto.randomBytes(16);
        return bytes.toString('base64');
    }
    
    private async pbkdf2(password: string, salt: string, iterations: number): Promise<string> {
        return new Promise((resolve, reject) => {
            crypto.pbkdf2(
                password,
                salt,
                iterations,
                64,
                'sha512',
                (err, derivedKey) => {
                    if (err) reject(err);
                    else resolve(derivedKey.toString('hex'));
                }
            );
        });
    }
    
    private async hmacSHA256(data: string, key: string): Promise<string> {
        const hmac = crypto.createHmac('sha256', key);
        hmac.update(data);
        return this.base64UrlEncode(hmac.digest());
    }
    
    private base64UrlEncode(str: string | Buffer): string {
        const base64 = Buffer.isBuffer(str) 
            ? str.toString('base64') 
            : Buffer.from(str).toString('base64');
        return base64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
    }
    
    private base64UrlDecode(str: string): string {
        const padding = '='.repeat((4 - str.length % 4) % 4);
        const base64 = str.replace(/-/g, '+').replace(/_/g, '/') + padding;
        return Buffer.from(base64, 'base64').toString('utf8');
    }
    
    private timingSafeEqual(a: string, b: string): boolean {
        if (a.length !== b.length) return false;
        
        let result = 0;
        for (let i = 0; i < a.length; i++) {
            result |= a.charCodeAt(i) ^ b.charCodeAt(i);
        }
        return result === 0;
    }
}
