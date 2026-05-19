/**
 * DataCacheService.ets
 * Caching service for application data
 */

import { Storage } from '@ohos.data.storage';
import { Logger } from '../utils/Logger';

/**
 * Cache entry with expiration
 */
interface CacheEntry<T> {
    data: T;
    timestamp: number;
    expiresIn: number;
}

/**
 * Generic data cache service
 */
export class DataCacheService {
    private storage: Storage;
    private logger: Logger;
    private memoryCache: Map<string, CacheEntry<any>> = new Map();
    private defaultTTL: number = 5 * 60 * 1000; // 5 minutes

    constructor(storage: Storage, logger: Logger) {
        this.storage = storage;
        this.logger = logger;
    }

    /**
     * Store data in cache
     */
    async set<T>(key: string, data: T, ttl?: number): Promise<void> {
        const entry: CacheEntry<T> = {
            data,
            timestamp: Date.now(),
            expiresIn: ttl || this.defaultTTL
        };

        // Store in memory
        this.memoryCache.set(key, entry);

        // Persist to storage
        try {
            await this.storage.put(key, JSON.stringify(entry));
            await this.storage.flush();
            this.logger.debug(`Cache set: ${key}`);
        } catch (error) {
            this.logger.error(`Failed to persist cache: ${error.message}`);
        }
    }

    /**
     * Retrieve data from cache
     */
    async get<T>(key: string): Promise<T | null> {
        // Check memory cache first
        const memoryEntry = this.memoryCache.get(key);
        if (memoryEntry && !this.isExpired(memoryEntry)) {
            this.logger.debug(`Cache hit (memory): ${key}`);
            return memoryEntry.data;
        }

        // Check persistent storage
        try {
            const stored = await this.storage.get(key, '');
            if (stored) {
                const entry: CacheEntry<T> = JSON.parse(stored);
                if (!this.isExpired(entry)) {
                    // Restore to memory
                    this.memoryCache.set(key, entry);
                    this.logger.debug(`Cache hit (storage): ${key}`);
                    return entry.data;
                } else {
                    // Remove expired entry
                    await this.remove(key);
                }
            }
        } catch (error) {
            this.logger.error(`Cache retrieval error: ${error.message}`);
        }

        this.logger.debug(`Cache miss: ${key}`);
        return null;
    }

    /**
     * Remove item from cache
     */
    async remove(key: string): Promise<void> {
        this.memoryCache.delete(key);
        try {
            await this.storage.delete(key);
            await this.storage.flush();
            this.logger.debug(`Cache removed: ${key}`);
        } catch (error) {
            this.logger.error(`Failed to remove cache: ${error.message}`);
        }
    }

    /**
     * Clear all cache
     */
    async clear(): Promise<void> {
        this.memoryCache.clear();
        try {
            await this.storage.clear();
            this.logger.info('Cache cleared');
        } catch (error) {
            this.logger.error(`Failed to clear cache: ${error.message}`);
        }
    }

    /**
     * Check if cache entry is expired
     */
    private isExpired(entry: CacheEntry<any>): boolean {
        return Date.now() - entry.timestamp > entry.expiresIn;
    }

    /**
     * Get cache statistics
     */
    getStats(): { memory: number; keys: string[] } {
        return {
            memory: this.memoryCache.size,
            keys: Array.from(this.memoryCache.keys())
        };
    }
}

/**
 * Cache configuration options
 */
export interface CacheConfig {
    ttl?: number;
    maxSize?: number;
    persistent?: boolean;
}

/**
 * Create cache service with config
 */
export function createCacheService(
    storage: Storage, 
    logger: Logger,
    config?: CacheConfig
): DataCacheService {
    const service = new DataCacheService(storage, logger);
    if (config?.ttl) {
        // Would set TTL in real implementation
    }
    return service;
}
