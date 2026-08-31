alter type public.job_status add value if not exists 'recovering';
alter type public.job_status add value if not exists 'waiting_resources';
alter type public.job_status add value if not exists 'needs_input';
