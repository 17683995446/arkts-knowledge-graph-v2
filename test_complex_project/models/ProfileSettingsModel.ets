/**
 * ProfileSettingsModel.ets
 * User profile settings and preferences
 */

import { BaseEntity, Nullable } from '../types/CommonTypes';

/**
 * User profile settings model
 */
export class ProfileSettingsModel implements BaseEntity {
    id: string;
    userId: string;
    createdAt: number;
    updatedAt: number;
    version: number;
    
    // Personal info
    firstName: Nullable<string>;
    lastName: Nullable<string>;
    bio: Nullable<string>;
    birthday: Nullable<number>;
    gender: Nullable<'male' | 'female' | 'other' | 'prefer_not_to_say'>;
    
    // Contact
    phoneNumber: Nullable<string>;
    secondaryEmail: Nullable<string>;
    
    // Social links
    socialLinks: SocialLinks;
    
    // Professional
    occupation: Nullable<string>;
    company: Nullable<string>;
    website: Nullable<string>;
    
    constructor(data: Partial<ProfileSettingsModel>) {
        Object.assign(this, data);
        this.socialLinks = data.socialLinks || {};
        this.createdAt = data.createdAt || Date.now();
        this.updatedAt = data.updatedAt || Date.now();
        this.version = data.version || 1;
    }
    
    /**
     * Get full name
     */
    getFullName(): string {
        if (this.firstName && this.lastName) {
            return `${this.firstName} ${this.lastName}`;
        }
        return this.firstName || this.lastName || '';
    }
    
    /**
     * Update social links
     */
    updateSocialLinks(links: Partial<SocialLinks>): void {
        Object.assign(this.socialLinks, links);
        this.touch();
    }
    
    /**
     * Validate profile completeness
     */
    getCompletenessScore(): number {
        let score = 0;
        let total = 0;
        
        const fields = [
            this.firstName,
            this.lastName,
            this.bio,
            this.phoneNumber,
            this.occupation,
            this.company
        ];
        
        total = fields.length;
        score = fields.filter(f => f && f.length > 0).length;
        
        // Social links bonus
        if (Object.keys(this.socialLinks).length > 0) {
            score += 1;
        }
        total += 1;
        
        return Math.round((score / total) * 100);
    }
    
    private touch(): void {
        this.updatedAt = Date.now();
        this.version++;
    }
}

/**
 * Social media links
 */
export interface SocialLinks {
    twitter?: string;
    linkedin?: string;
    github?: string;
    facebook?: string;
    instagram?: string;
    weibo?: string;
    wechat?: string;
}
