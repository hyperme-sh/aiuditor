"""Owner-local event delivery without another service's SQL tables."""
from alembic import op

revision = 'aiuditor_0061_owned_outbox'
down_revision = 'aiuditor_0060_admission_reports'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''CREATE TABLE public.service_tenants (
        tenant_id uuid PRIMARY KEY REFERENCES public.tenants(id)
    )''')
    op.execute('''CREATE TABLE public.service_outbox (
        id uuid PRIMARY KEY,
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        envelope jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        delivered_at timestamptz
    )''')
    op.execute('CREATE INDEX service_outbox_pending ON public.service_outbox(created_at,id) WHERE delivered_at IS NULL')
    op.execute('ALTER TABLE public.service_outbox ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE public.service_outbox FORCE ROW LEVEL SECURITY')
    op.execute('''CREATE POLICY service_outbox_tenant ON public.service_outbox
        USING (tenant_id = nullif(current_setting('app.current_tenant_id',true),'')::uuid)
        WITH CHECK (tenant_id = nullif(current_setting('app.current_tenant_id',true),'')::uuid)''')
    op.execute('''CREATE FUNCTION public.aiuditor_remember_event_tenant() RETURNS trigger
        LANGUAGE plpgsql SET search_path=public,pg_catalog AS $$
        BEGIN
            INSERT INTO public.service_tenants VALUES(NEW.tenant_id) ON CONFLICT DO NOTHING;
            RETURN NEW;
        END;
        $$''')
    op.execute('''CREATE TRIGGER remember_event_tenant AFTER INSERT ON public.service_outbox
        FOR EACH ROW EXECUTE FUNCTION public.aiuditor_remember_event_tenant()''')


def downgrade():
    raise RuntimeError('Preserve durable event obligations; use a reviewed database restore')
