/**
 * AuthenticationService.ets
 * Handles user authentication and session management
 */

import { UserEntityModel } from '../models/UserEntityModel';
import { ApiResponse, ApiError } from '../types/CommonTypes';
import { TokenManagerService } from './TokenManagerService';
import { UserRepositoryService } from './UserRepositoryService';
import { EncryptionUtility } from '../utils/EncryptionUtility';
import { LoggerUtility } from '../utils/LoggerUtility';

/**
 * Authentication service for login/logout operations
 */
export class AuthenticationService {
    private tokenManager: TokenManagerService;
    private userRepository: UserRepositoryService;
    private encryption: EncryptionUtility;
    private logger: LoggerUtility;
    
    // Session storage
    private activeSessions: Map<string, SessionInfo> = new Map();
    private readonly SESSION_TIMEOUT = 24 * 60 * 60 * 1000; // 24 hours
    
    constructor(
        tokenManager: TokenManagerService,
        userRepository: UserRepositoryService,
        encryption: EncryptionUtility,
        logger: LoggerUtility
    ) {
        this.tokenManager = tokenManager;
        this.userRepository = userRepository;
        this.encryption = encryption;
        this.logger = logger;
    }
    
    /**
     * Authenticate user with credentials
     */
    async login(credentials: LoginCredentials): Promise<ApiResponse<AuthResult>> {
        try {
            this.logger.info(`Login attempt for: ${credentials.email}`);
            
            // Find user by email
            const user = await this.userRepository.findByEmail(credentials.email);
            if (!user) {
                this.logger.warn(`User not found: ${credentials.email}`);
                return this.createErrorResponse('USER_NOT_FOUND', 'Invalid credentials');
            }
            
            // Check if account is locked
            if (user.isLocked()) {
                this.logger.warn(`Account locked: ${credentials.email}`);
                return this.createErrorResponse('ACCOUNT_LOCKED', 'Account is temporarily locked');
            }
            
            // Verify password
            const passwordValid = await this.verifyPassword(
                credentials.password,
                user.passwordHash
            );
            
            if (!passwordValid) {
                user.recordLogin(false);
                await this.userRepository.update(user);
                this.logger.warn(`Invalid password for: ${credentials.email}`);
                return this.createErrorResponse('INVALID_CREDENTIALS', 'Invalid credentials');
            }
            
            // Check if email is verified
            if (!user.emailVerified) {
                this.logger.warn(`Email not verified: ${credentials.email}`);
                return this.createErrorResponse('EMAIL_NOT_VERIFIED', 'Please verify your email');
            }
            
            // Generate tokens
            const accessToken = await this.tokenManager.generateAccessToken(user);
            const refreshToken = await this.tokenManager.generateRefreshToken(user);
            
            // Record successful login
            user.recordLogin(true);
            await this.userRepository.update(user);
            
            // Create session
            const sessionId = this.createSession(user.id, accessToken);
            
            this.logger.info(`Login successful: ${credentials.email}`);
            
            return {
                success: true,
                data: {
                    user: user.toJSON(),
                    accessToken,
                    refreshToken,
                    sessionId,
                    expiresIn: this.SESSION_TIMEOUT
                }
            };
            
        } catch (error) {
            this.logger.error(`Login error: ${error.message}`);
            return this.createErrorResponse('INTERNAL_ERROR', 'Authentication failed');
        }
    }
    
    /**
     * Logout user and invalidate session
     */
    async logout(sessionId: string): Promise<ApiResponse<void>> {
        try {
            this.activeSessions.delete(sessionId);
            this.logger.info(`Session terminated: ${sessionId}`);
            return { success: true };
        } catch (error) {
            this.logger.error(`Logout error: ${error.message}`);
            return this.createErrorResponse('INTERNAL_ERROR', 'Logout failed');
        }
    }
    
    /**
     * Refresh access token
     */
    async refreshToken(refreshToken: string): Promise<ApiResponse<TokenRefreshResult>> {
        try {
            const payload = await this.tokenManager.verifyRefreshToken(refreshToken);
            if (!payload) {
                return this.createErrorResponse('INVALID_TOKEN', 'Invalid refresh token');
            }
            
            const user = await this.userRepository.findById(payload.userId);
            if (!user || !user.isActive()) {
                return this.createErrorResponse('USER_INVALID', 'User not found or inactive');
            }
            
            const newAccessToken = await this.tokenManager.generateAccessToken(user);
            
            return {
                success: true,
                data: {
                    accessToken: newAccessToken,
                    expiresIn: this.SESSION_TIMEOUT
                }
            };
        } catch (error) {
            this.logger.error(`Token refresh error: ${error.message}`);
            return this.createErrorResponse('INTERNAL_ERROR', 'Token refresh failed');
        }
    }
    
    /**
     * Verify if session is valid
     */
    async validateSession(sessionId: string): Promise<boolean> {
        const session = this.activeSessions.get(sessionId);
        if (!session) return false;
        
        if (Date.now() > session.expiresAt) {
            this.activeSessions.delete(sessionId);
            return false;
        }
        
        return true;
    }
    
    /**
     * Change user password
     */
    async changePassword(
        userId: string,
        currentPassword: string,
        newPassword: string
    ): Promise<ApiResponse<void>> {
        try {
            const user = await this.userRepository.findById(userId);
            if (!user) {
                return this.createErrorResponse('USER_NOT_FOUND', 'User not found');
            }
            
            const passwordValid = await this.verifyPassword(
                currentPassword,
                user.passwordHash
            );
            
            if (!passwordValid) {
                return this.createErrorResponse('INVALID_PASSWORD', 'Current password is incorrect');
            }
            
            const newHash = await this.hashPassword(newPassword);
            user.passwordHash = newHash;
            await this.userRepository.update(user);
            
            this.logger.info(`Password changed for user: ${userId}`);
            return { success: true };
            
        } catch (error) {
            this.logger.error(`Password change error: ${error.message}`);
            return this.createErrorResponse('INTERNAL_ERROR', 'Password change failed');
        }
    }
    
    /**
     * Request password reset
     */
    async requestPasswordReset(email: string): Promise<ApiResponse<void>> {
        try {
            const user = await this.userRepository.findByEmail(email);
            if (!user) {
                // Don't reveal if email exists
                return { success: true };
            }
            
            const resetToken = await this.tokenManager.generatePasswordResetToken(user);
            // In real implementation, send email with reset link
            this.logger.info(`Password reset requested for: ${email}`);
            
            return { success: true };
        } catch (error) {
            this.logger.error(`Password reset request error: ${error.message}`);
            return this.createErrorResponse('INTERNAL_ERROR', 'Request failed');
        }
    }
    
    /**
     * Clean up expired sessions
     */
    cleanupExpiredSessions(): number {
        const now = Date.now();
        let cleaned = 0;
        
        for (const [sessionId, session] of this.activeSessions) {
            if (now > session.expiresAt) {
                this.activeSessions.delete(sessionId);
                cleaned++;
            }
        }
        
        if (cleaned > 0) {
            this.logger.info(`Cleaned up ${cleaned} expired sessions`);
        }
        
        return cleaned;
    }
    
    // Private helper methods
    
    private async verifyPassword(plain: string, hash: string): Promise<boolean> {
        return this.encryption.compare(plain, hash);
    }
    
    private async hashPassword(password: string): Promise<string> {
        return this.encryption.hash(password);
    }
    
    private createSession(userId: string, token: string): string {
        const sessionId = this.encryption.generateRandomId();
        this.activeSessions.set(sessionId, {
            userId,
            token,
            createdAt: Date.now(),
            expiresAt: Date.now() + this.SESSION_TIMEOUT
        });
        return sessionId;
    }
    
    private createErrorResponse(code: string, message: string): ApiResponse<never> {
        return {
            success: false,
            error: { code, message }
        };
    }
}

/**
 * Login credentials interface
 */
interface LoginCredentials {
    email: string;
    password: string;
    rememberMe?: boolean;
}

/**
 * Authentication result
 */
interface AuthResult {
    user: object;
    accessToken: string;
    refreshToken: string;
    sessionId: string;
    expiresIn: number;
}

/**
 * Token refresh result
 */
interface TokenRefreshResult {
    accessToken: string;
    expiresIn: number;
}

/**
 * Session information
 */
interface SessionInfo {
    userId: string;
    token: string;
    createdAt: number;
    expiresAt: number;
}
