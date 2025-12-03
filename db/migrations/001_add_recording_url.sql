-- Migration: add recording_url to calls table
ALTER TABLE IF EXISTS public.calls
ADD COLUMN IF NOT EXISTS recording_url TEXT;
