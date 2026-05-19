/**
 * UserProfileManager.ets
 * Example ArkTS file for testing the knowledge graph system
 */

import { UserInfo } from '../models/UserInfo';
import { ApiService } from '../services/ApiService';
import { Logger } from '../utils/Logger';

/**
 * Manages user profile data and operations
 */
export class UserProfileManager {
    private userInfo: UserInfo | null = null;
    private apiService: ApiService;
    private logger: Logger;

    constructor(apiService: ApiService, logger: Logger) {
        this.apiService = apiService;
        this.logger = logger;
    }

    /**
     * Load user profile from server
     */
    async loadUserProfile(userId: string): Promise<UserInfo | null> {
        try {
            this.logger.info(`Loading profile for user: ${userId}`);
            const response = await this.apiService.getUserInfo(userId);
            
            if (response.success) {
                this.userInfo = response.data;
                this.logger.info('Profile loaded successfully');
                return this.userInfo;
            } else {
                this.logger.error('Failed to load profile');
                return null;
            }
        } catch (error) {
            this.logger.error(`Error loading profile: ${error.message}`);
            return null;
        }
    }

    /**
     * Update user profile
     */
    async updateProfile(updates: Partial<UserInfo>): Promise<boolean> {
        if (!this.userInfo) {
            this.logger.error('No user loaded');
            return false;
        }

        try {
            const updatedInfo = { ...this.userInfo, ...updates };
            const response = await this.apiService.updateUserInfo(
                this.userInfo.id, 
                updatedInfo
            );

            if (response.success) {
                this.userInfo = response.data;
                this.logger.info('Profile updated successfully');
                return true;
            }
            return false;
        } catch (error) {
            this.logger.error(`Error updating profile: ${error.message}`);
            return false;
        }
    }

    /**
     * Get current user info
     */
    getCurrentUser(): UserInfo | null {
        return this.userInfo;
    }

    /**
     * Check if user is authenticated
     */
    isAuthenticated(): boolean {
        return this.userInfo !== null;
    }

    /**
     * Clear user data
     */
    clearUser(): void {
        this.userInfo = null;
        this.logger.info('User data cleared');
    }
}

/**
 * Profile update event interface
 */
interface ProfileUpdateEvent {
    userId: string;
    timestamp: number;
    changes: string[];
}

/**
 * Profile manager factory
 */
export function createProfileManager(
    apiService: ApiService, 
    logger: Logger
): UserProfileManager {
    return new UserProfileManager(apiService, logger);
}
