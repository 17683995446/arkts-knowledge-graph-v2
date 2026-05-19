/**
 * UserRepositoryService.ets
 * Data access layer for user entities
 */

import { UserEntityModel } from '../models/UserEntityModel';
import { ProfileSettingsModel } from '../models/ProfileSettingsModel';
import { AddressEntityModel } from '../models/AddressEntityModel';
import { ApiResponse, PaginatedResponse, PaginationParams, EntityStatus } from '../types/CommonTypes';
import { DatabaseConnection } from '../utils/DatabaseConnection';
import { LoggerUtility } from '../utils/LoggerUtility';

/**
 * Repository for user data operations
 */
export class UserRepositoryService {
    private db: DatabaseConnection;
    private logger: LoggerUtility;
    private cache: Map<string, UserEntityModel> = new Map();
    private readonly CACHE_TTL = 5 * 60 * 1000; // 5 minutes
    
    constructor(db: DatabaseConnection, logger: LoggerUtility) {
        this.db = db;
        this.logger = logger;
    }
    
    /**
     * Find user by ID
     */
    async findById(id: string): Promise<UserEntityModel | null> {
        // Check cache first
        const cached = this.getFromCache(id);
        if (cached) return cached;
        
        try {
            const result = await this.db.query(
                `SELECT u.*, p.* FROM users u 
                 LEFT JOIN profiles p ON u.id = p.userId 
                 WHERE u.id = ? AND u.status != ?`,
                [id, EntityStatus.DELETED]
            );
            
            if (result.length === 0) return null;
            
            const user = this.hydrateUser(result[0]);
            this.setCache(id, user);
            return user;
        } catch (error) {
            this.logger.error(`Error finding user by ID: ${error.message}`);
            throw error;
        }
    }
    
    /**
     * Find user by email
     */
    async findByEmail(email: string): Promise<UserEntityModel | null> {
        try {
            const result = await this.db.query(
                `SELECT u.*, p.* FROM users u 
                 LEFT JOIN profiles p ON u.id = p.userId 
                 WHERE u.email = ? AND u.status != ?`,
                [email, EntityStatus.DELETED]
            );
            
            if (result.length === 0) return null;
            
            return this.hydrateUser(result[0]);
        } catch (error) {
            this.logger.error(`Error finding user by email: ${error.message}`);
            throw error;
        }
    }
    
    /**
     * Find user by username
     */
    async findByUsername(username: string): Promise<UserEntityModel | null> {
        try {
            const result = await this.db.query(
                `SELECT u.*, p.* FROM users u 
                 LEFT JOIN profiles p ON u.id = p.userId 
                 WHERE u.username = ? AND u.status != ?`,
                [username, EntityStatus.DELETED]
            );
            
            if (result.length === 0) return null;
            
            return this.hydrateUser(result[0]);
        } catch (error) {
            this.logger.error(`Error finding user by username: ${error.message}`);
            throw error;
        }
    }
    
    /**
     * Search users with pagination
     */
    async search(
        query: string,
        params: PaginationParams
    ): Promise<PaginatedResponse<UserEntityModel>> {
        try {
            const offset = (params.page - 1) * params.pageSize;
            
            const countResult = await this.db.query(
                `SELECT COUNT(*) as total FROM users 
                 WHERE (email LIKE ? OR username LIKE ? OR displayName LIKE ?) 
                 AND status != ?`,
                [`%${query}%`, `%${query}%`, `%${query}%`, EntityStatus.DELETED]
            );
            
            const total = countResult[0].total;
            
            const results = await this.db.query(
                `SELECT u.*, p.* FROM users u 
                 LEFT JOIN profiles p ON u.id = p.userId 
                 WHERE (u.email LIKE ? OR u.username LIKE ? OR u.displayName LIKE ?) 
                 AND u.status != ?
                 ORDER BY u.${params.sortBy || 'createdAt'} ${params.sortOrder || 'DESC'}
                 LIMIT ? OFFSET ?`,
                [`%${query}%`, `%${query}%`, `%${query}%`, EntityStatus.DELETED, 
                 params.pageSize, offset]
            );
            
            const users = results.map(r => this.hydrateUser(r));
            
            return {
                data: users,
                total,
                page: params.page,
                pageSize: params.pageSize,
                hasMore: offset + users.length < total
            };
        } catch (error) {
            this.logger.error(`Error searching users: ${error.message}`);
            throw error;
        }
    }
    
    /**
     * Create new user
     */
    async create(user: UserEntityModel): Promise<UserEntityModel> {
        try {
            await this.db.transaction(async (trx) => {
                // Insert user
                await trx.execute(
                    `INSERT INTO users (id, email, username, displayName, passwordHash, 
                     role, status, emailVerified, createdAt, updatedAt, version)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
                    [user.id, user.email, user.username, user.displayName, user.passwordHash,
                     user.role, user.status, user.emailVerified, user.createdAt, 
                     user.updatedAt, user.version]
                );
                
                // Insert profile
                if (user.profile) {
                    await trx.execute(
                        `INSERT INTO profiles (id, userId, firstName, lastName, createdAt, updatedAt)
                         VALUES (?, ?, ?, ?, ?, ?)`,
                        [user.profile.id, user.id, user.profile.firstName, 
                         user.profile.lastName, user.profile.createdAt, user.profile.updatedAt]
                    );
                }
            });
            
            this.logger.info(`User created: ${user.id}`);
            this.setCache(user.id, user);
            return user;
        } catch (error) {
            this.logger.error(`Error creating user: ${error.message}`);
            throw error;
        }
    }
    
    /**
     * Update existing user
     */
    async update(user: UserEntityModel): Promise<UserEntityModel> {
        try {
            await this.db.execute(
                `UPDATE users SET 
                 email = ?, username = ?, displayName = ?, passwordHash = ?,
                 role = ?, status = ?, emailVerified = ?, phoneVerified = ?,
                 lastLoginAt = ?, loginAttempts = ?, lockedUntil = ?,
                 updatedAt = ?, version = ?
                 WHERE id = ?`,
                [user.email, user.username, user.displayName, user.passwordHash,
                 user.role, user.status, user.emailVerified, user.phoneVerified,
                 user.lastLoginAt, user.loginAttempts, user.lockedUntil,
                 user.updatedAt, user.version, user.id]
            );
            
            this.invalidateCache(user.id);
            this.logger.info(`User updated: ${user.id}`);
            return user;
        } catch (error) {
            this.logger.error(`Error updating user: ${error.message}`);
            throw error;
        }
    }
    
    /**
     * Soft delete user
     */
    async delete(id: string): Promise<boolean> {
        try {
            const result = await this.db.execute(
                `UPDATE users SET status = ?, updatedAt = ? WHERE id = ?`,
                [EntityStatus.DELETED, Date.now(), id]
            );
            
            this.invalidateCache(id);
            this.logger.info(`User deleted: ${id}`);
            return result.affectedRows > 0;
        } catch (error) {
            this.logger.error(`Error deleting user: ${error.message}`);
            throw error;
        }
    }
    
    /**
     * Get user addresses
     */
    async getUserAddresses(userId: string): Promise<AddressEntityModel[]> {
        try {
            const results = await this.db.query(
                `SELECT * FROM addresses WHERE userId = ? AND status != ?
                 ORDER BY isDefault DESC, createdAt DESC`,
                [userId, EntityStatus.DELETED]
            );
            
            return results.map(r => new AddressEntityModel(r));
        } catch (error) {
            this.logger.error(`Error getting user addresses: ${error.message}`);
            throw error;
        }
    }
    
    /**
     * Count active users
     */
    async countActive(): Promise<number> {
        try {
            const result = await this.db.query(
                `SELECT COUNT(*) as count FROM users WHERE status = ?`,
                [EntityStatus.ACTIVE]
            );
            return result[0].count;
        } catch (error) {
            this.logger.error(`Error counting users: ${error.message}`);
            throw error;
        }
    }
    
    // Private methods
    
    private hydrateUser(data: any): UserEntityModel {
        const profile = new ProfileSettingsModel({
            id: data.profileId,
            userId: data.id,
            firstName: data.firstName,
            lastName: data.lastName,
            createdAt: data.profileCreatedAt,
            updatedAt: data.profileUpdatedAt
        });
        
        return new UserEntityModel({
            ...data,
            profile
        });
    }
    
    private getFromCache(id: string): UserEntityModel | null {
        const cached = this.cache.get(id);
        if (!cached) return null;
        
        // Check if cache entry has metadata with expiry
        const cacheEntry = this.cache.get(`_meta_${id}`) as { expiry: number } | undefined;
        if (cacheEntry && Date.now() > cacheEntry.expiry) {
            this.invalidateCache(id);
            return null;
        }
        
        return cached;
    }
    
    private setCache(id: string, user: UserEntityModel): void {
        this.cache.set(id, user);
        this.cache.set(`_meta_${id}`, { expiry: Date.now() + this.CACHE_TTL });
    }
    
    private invalidateCache(id: string): void {
        this.cache.delete(id);
        this.cache.delete(`_meta_${id}`);
    }
}
