import { pathToFileURL } from 'node:url';
const adapter = process.env.KANBAN_ADAPTER;
const missing = () => { throw new Error('Managed automation requires KANBAN_ADAPTER'); };
const implementation = adapter ? await import(pathToFileURL(adapter).href) : {};
export const instanceInfo = implementation.instanceInfo || missing;
export const claimOwner = implementation.claimOwner || missing;
export const privatePath = implementation.privatePath || missing;
export const hashPlan = implementation.hashPlan || missing;
export const verifyReport = implementation.verifyReport || missing;
