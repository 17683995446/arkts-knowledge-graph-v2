/**
 * CommonTypes.ets
 * Shared type definitions across the application
 */

/**
 * Base entity interface for all domain models
 */
export interface BaseEntity {
    id: string;
    createdAt: number;
    updatedAt: number;
    version: number;
}

/**
 * Pagination parameters
 */
export interface PaginationParams {
    page: number;
    pageSize: number;
    sortBy?: string;
    sortOrder?: 'asc' | 'desc';
}

/**
 * Paginated response wrapper
 */
export interface PaginatedResponse<T> {
    data: T[];
    total: number;
    page: number;
    pageSize: number;
    hasMore: boolean;
}

/**
 * API response standard format
 */
export interface ApiResponse<T> {
    success: boolean;
    data?: T;
    error?: ApiError;
    metadata?: ResponseMetadata;
}

/**
 * API error structure
 */
export interface ApiError {
    code: string;
    message: string;
    details?: Record<string, string[]>;
    stack?: string;
}

/**
 * Response metadata
 */
export interface ResponseMetadata {
    requestId: string;
    timestamp: number;
    duration: number;
    cacheHit?: boolean;
}

/**
 * User roles enum
 */
export enum UserRole {
    ADMIN = 'admin',
    MANAGER = 'manager',
    USER = 'user',
    GUEST = 'guest'
}

/**
 * Permission definitions
 */
export enum Permission {
    READ = 'read',
    WRITE = 'write',
    DELETE = 'delete',
    MANAGE = 'manage'
}

/**
 * Status enum for various entities
 */
export enum EntityStatus {
    ACTIVE = 'active',
    INACTIVE = 'inactive',
    PENDING = 'pending',
    ARCHIVED = 'archived',
    DELETED = 'deleted'
}

/**
 * Nullable type helper
 */
export type Nullable<T> = T | null;

/**
 * Optional type helper
 */
export type Optional<T> = T | undefined;

/**
 * Deep partial type
 */
export type DeepPartial<T> = {
    [P in keyof T]?: T[P] extends object ? DeepPartial<T[P]> : T[P];
};

/**
 * Event handler type
 */
export type EventHandler<T = void> = (event: T) => void | Promise<void>;

/**
 * Async function type
 */
export type AsyncFunction<T = void, R = void> = (arg: T) => Promise<R>;
