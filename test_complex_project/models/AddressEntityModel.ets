/**
 * AddressEntityModel.ets
 * Address entity for user locations
 */

import { BaseEntity, EntityStatus } from '../types/CommonTypes';

/**
 * Address entity representing physical location
 */
export class AddressEntityModel implements BaseEntity {
    id: string;
    userId: string;
    createdAt: number;
    updatedAt: number;
    version: number;
    
    // Address fields
    label: string; // home, work, etc.
    recipientName: string;
    phoneNumber: string;
    
    // Location
    country: string;
    province: string;
    city: string;
    district: string;
    street: string;
    postalCode: string;
    
    // Coordinates
    latitude: number;
    longitude: number;
    
    // Status
    isDefault: boolean;
    status: EntityStatus;
    
    constructor(data: Partial<AddressEntityModel>) {
        Object.assign(this, data);
        this.createdAt = data.createdAt || Date.now();
        this.updatedAt = data.updatedAt || Date.now();
        this.version = data.version || 1;
    }
    
    /**
     * Get formatted address string
     */
    getFormattedAddress(): string {
        const parts = [
            this.country,
            this.province,
            this.city,
            this.district,
            this.street
        ].filter(Boolean);
        return parts.join(', ');
    }
    
    /**
     * Set as default address
     */
    setAsDefault(): void {
        this.isDefault = true;
        this.touch();
    }
    
    /**
     * Validate address completeness
     */
    isValid(): boolean {
        return !!(
            this.recipientName &&
            this.phoneNumber &&
            this.country &&
            this.city &&
            this.street
        );
    }
    
    private touch(): void {
        this.updatedAt = Date.now();
        this.version++;
    }
}
