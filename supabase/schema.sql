create table if not exists public.jobs (
    id                uuid primary key default gen_random_uuid(),
    user_id           uuid not null references auth.users (id) on delete cascade,
    conversion_type   text not null check (conversion_type in ('pdf-to-excel', 'csv-to-excel')),
    original_name     text not null,
    source_path       text,
    source_size_bytes bigint,
    result_name       text,
    result_path       text,
    output_size_bytes bigint,
    status            text not null default 'processing'
                      check (status in ('processing', 'completed', 'failed')),
    error             text,
    page_count        integer,
    row_count         integer,
    duration_ms       integer,
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);

create index if not exists jobs_user_created_idx
    on public.jobs (user_id, created_at desc);

create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists jobs_touch_updated_at on public.jobs;
create trigger jobs_touch_updated_at
    before update on public.jobs
    for each row execute function public.touch_updated_at();

alter table public.jobs enable row level security;

drop policy if exists jobs_select_own on public.jobs;
create policy jobs_select_own on public.jobs
    for select using (auth.uid() = user_id);

drop policy if exists jobs_insert_own on public.jobs;
create policy jobs_insert_own on public.jobs
    for insert with check (auth.uid() = user_id);

drop policy if exists jobs_update_own on public.jobs;
create policy jobs_update_own on public.jobs
    for update using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists jobs_delete_own on public.jobs;
create policy jobs_delete_own on public.jobs
    for delete using (auth.uid() = user_id);

grant usage on schema public to anon, authenticated;
grant select, insert, update, delete on public.jobs to authenticated;

insert into storage.buckets (id, name, public)
values ('sources', 'sources', false)
on conflict (id) do nothing;

insert into storage.buckets (id, name, public)
values ('results', 'results', false)
on conflict (id) do nothing;

drop policy if exists sources_select_own on storage.objects;
create policy sources_select_own on storage.objects
    for select using (
        bucket_id = 'sources'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

drop policy if exists sources_insert_own on storage.objects;
create policy sources_insert_own on storage.objects
    for insert with check (
        bucket_id = 'sources'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

drop policy if exists sources_delete_own on storage.objects;
create policy sources_delete_own on storage.objects
    for delete using (
        bucket_id = 'sources'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

drop policy if exists results_select_own on storage.objects;
create policy results_select_own on storage.objects
    for select using (
        bucket_id = 'results'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

drop policy if exists results_insert_own on storage.objects;
create policy results_insert_own on storage.objects
    for insert with check (
        bucket_id = 'results'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

drop policy if exists results_delete_own on storage.objects;
create policy results_delete_own on storage.objects
    for delete using (
        bucket_id = 'results'
        and (storage.foldername(name))[1] = auth.uid()::text
    );
