import {createContext,useContext} from 'react'
import type {TenantScope} from '@/lib/forsic-tenant'
export const TenantContext=createContext<TenantScope|null>(null)
export function useTenant(){return useContext(TenantContext)}
