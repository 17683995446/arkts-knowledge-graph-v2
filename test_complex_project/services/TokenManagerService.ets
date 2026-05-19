/**
 * TokenManagerService.ets
 * JWT token generation and verification
 */

import { UserEntityModel } from '../models/UserEntityModel';
import { EncryptionUtility } from '../utils/EncryptionUtility';

/**
 * Token payload structure
 */
interface TokenPayload {
    userId: string;
    email: string;
    role: string;
    iat: number;
    exp: number;
    type: 'access' | 'refresh' | 'reset';
}

/**
 * Token manager for JWT operations
 */
export class TokenManagerService {
    private encryption: EncryptionUtility;
    private readonly ACCESS_TOKEN_EXPIRY = 15 * 60; // 15 minutes in seconds
    private readonly REFRESH_TOKEN_EXPIRY = 7 * 24 * 60 * 60; // 7 days
    private readonly RESET_TOKEN_EXPIRY = 60 * 60; // 1 hour
    
    constructor(encryption: EncryptionUtility) {
        this.encryption = encryption;
    }
    
    /**
     * Generate access token for user
     */
    async generateAccessToken(user: UserEntityModel): Promise<string> {
        const payload: TokenPayload = {
            userId: user.id,
            email: user.email,
            role: user.role,
            iat: Math.floor(Date.now() / 1000),
            exp: Math.floor(Date.now() / 1000) + this.ACCESS_TOKEN_EXPIRY,
            type: 'access'
        };
        
        return this.encryption.signJWT(payload);
    }
    
    /**
     * Generate refresh token
     */
    async generateRefreshToken(user: UserEntityModel): Promise<string> {
        const payload: TokenPayload = {
            userId: user.id,
            email: user.email,
            role: user.role,
            iat: Math.floor(Date.now() / 1000),
            exp: Math.floor(Date.now() / 1000) + this.REFRESH_TOKEN_EXPIRY,
            type: 'refresh'
        };
        
        return this.encryption.signJWT(payload);
    }
    
    /**
     * Generate password reset token
     */
    async generatePasswordResetToken(user: UserEntityModel): Promise<string> {
        const payload: TokenPayload = {
            userId: user.id,
            email: user.email,
            role: user.role,
            iat: Math.floor(Date.now() / 1000),
            exp: Math.floor(Date.now() / 1000) + this.RESET_TOKEN_EXPIRY,
            type: 'reset'
        };
        
        return this.encryption.signJWT(payload);
    }
    
    /**
     * Verify access token
     */
    async verifyAccessToken(token: string): Promise<TokenPayload | null> {
        try {
            const payload = await this.encryption.verifyJWT(token);
            if (payload.type !== 'access') {
                return null;
            }
            return payload;
        } catch {
            return null;
        }
    }
    
    /**
     * Verify refresh token
     */
    async verifyRefreshToken(token: string): Promise<TokenPayload | null> {
        try {
            const payload = await this.encryption.verifyJWT(token);
            if (payload.type !== 'refresh') {
                return null;
            }
            return payload;
        } catch {
            return null;
        }
    }
    
    /**
     * Verify password reset token
     */
    async verifyPasswordResetToken(token: string): Promise<TokenPayload | null> {
        try {
            const payload = await this.encryption.verifyJWT(token);
            if (payload.type !== 'reset') {
                return null;
            }
            return payload;
        } catch {
            return null;
        }
    }
    
    /**
     * Decode token without verification
     */
    decodeToken(token: string): TokenPayload | null {
        try {
            return this.encryption.decodeJWT(token);
        } catch {
            return null;
        }
    }
    
    /**
     * Get token expiration time
     */
    getTokenExpiry(token: string): number | null {
        const payload = this.decodeToken(token);
        return payload ? payload.exp * 1000 : null;
    }
    
    /**
     * Check if token is expired
     */
    isTokenExpired(token: string): boolean {
        const expiry = this.getTokenExpiry(token);
        if (!expiry) return true;
        return Date.now() > expiry;
    }
    
    /**
     * Get time until token expires
     */
    getTimeUntilExpiry(token: string): number {
        const expiry = this.getTokenExpiry(token);
        if (!expiry) return 0;
        return Math.max(0, expiry - Date.now());
    }
}
