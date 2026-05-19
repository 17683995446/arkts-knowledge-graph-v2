//**
 * UserEntityModel.ets
 * User domain model with complex relationships
 */

import { BaseEntity, UserRole, EntityStatus, Nullable } from '../types/CommonTypes';
import { ProfileSettingsModel } from './ProfileSettingsModel';
import { AddressEntityModel } from './AddressEntityModel';

/**
 * User entity representing application user
 */
export class UserEntityModel implements BaseEntity {
    id: string;
    createdAt: number;
    updatedAt: number;
    version: number;
    
    // Basic info
    email: string;
    username: string;
    displayName: string;
    avatarUrl: Nullable<string>;
    
    // Security
    passwordHash: string;
    lastLoginAt: Nullable<number>;
    loginAttempts: number;
    lockedUntil: Nullable<number>;
    
    // Roles & Permissions
    role: UserRole;
    permissions: string[];
    
    // Status
    status: EntityStatus;
    emailVerified: boolean;
    phoneVerified: boolean;
    
    // Relationships
    profile: ProfileSettingsModel;
    addresses: AddressEntityModel[];
    preferences: UserPreferences;
    
    constructor(data: Partial<UserEntityModel>) {
        Object.assign(this, data);
        this.createdAt = data.createdAt || Date.now();
        this.updatedAt = data.updatedAt || Date.now();
        this.version = data.version || 1;
        this.addresses = data.addresses || [];
        this.permissions = data.permissions || [];
    }
    
    /**
     * Check if user has specific permission
     */
    hasPermission(permission: string): boolean {
        if (this.role === UserRole.ADMIN) return true;
        return this.permissions.includes(permission);
    }
    
    /**
     * Check if user account is active
     */
    isActive(): boolean {
        return this.status === EntityStatus.ACTIVE && !this.isLocked();
    }
    
    /**
     * Check if account is locked
     */
    isLocked(): boolean {
        if (!this.lockedUntil) return false;
        return Date.now() < this.lockedUntil;
    }
    
    /**
     * Update user profile
     */
    updateProfile(updates: Partial<ProfileSettingsModel>): void {
        Object.assign(this.profile, updates);
        this.touch();
    }
    
    /**
     * Add address to user
     */
    addAddress(address: AddressEntityModel): void {
        this.addresses.push(address);
        this.touch();
    }
    
    /**
     * Remove address by ID
     */
    removeAddress(addressId: string): boolean {
        const index = this.addresses.findIndex(a => a.id === addressId);
        if (index > -1) {
            this.addresses.splice(index, 1);
            this.touch();
            return true;
        }
        return false;
    }
    
    /**
     * Record login attempt
     */
    recordLogin(success: boolean): void {
        if (success) {
            this.loginAttempts = 0;
            this.lastLoginAt = Date.now();
        } else {
            this.loginAttempts++;
            if (this.loginAttempts >= 5) {
                this.lockedUntil = Date.now() + 30 * 60 * 1000; // 30 minutes
            }
        }
        this.touch();
    }
    
    /**
     * Update timestamp
     */
    private touch(): void {
        this.updatedAt = Date.now();
        this.version++;
    }
    
    /**
     * Convert to plain object
     */
    toJSON(): object {
        return {
            id: this.id,
            email: this.email,
            username: this.username,
            displayName: this.displayName,
            role: this.role,
            status: this.status,
            createdAt: this.createdAt,
            updatedAt: this.updatedAt
        };
    }
}

/**
 * User preferences sub-model
 */
export interface UserPreferences {
    theme: 'light' | 'dark' | 'auto';
    language: string;
    notifications: NotificationPreferences;
    privacy: PrivacyPreferences;
}

/**
 * Notification preferences
 */
export interface NotificationPreferences {
    email: boolean;
    push: boolean;
    sms: boolean;
    marketing: boolean;
}

/**
 * Privacy preferences
 */
export interface PrivacyPreferences {
    profileVisible: boolean;
    activityVisible: boolean;
    allowSearch: boolean;
}
