from datetime import datetime, timezone
from sqlalchemy import (Column, Integer, String, Text, DateTime, ForeignKey,
                        UniqueConstraint, Index)
from sqlalchemy.orm import relationship
from database import Base


def utcnow():
    return datetime.now(timezone.utc)


# --- Many-to-many join table ---
from sqlalchemy import Table

contact_tag = Table(
    'contact_tag', Base.metadata,
    Column('contact_id', Integer, ForeignKey('contact.id', ondelete='CASCADE'), primary_key=True),
    Column('tag_id', Integer, ForeignKey('tag.id', ondelete='CASCADE'), primary_key=True)
)


class Contact(Base):
    __tablename__ = 'contact'

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String, unique=True, nullable=False, index=True)
    first_name = Column(String, default='')
    last_name = Column(String, default='')
    company = Column(String, default='')
    title = Column(String, default='')
    notes = Column(Text, default='')
    status = Column(String, default='active')  # active, unsubscribed, bounced
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    tags = relationship('Tag', secondary=contact_tag, back_populates='contacts')
    campaign_contacts = relationship('CampaignContact', back_populates='contact')

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.email


class Tag(Base):
    __tablename__ = 'tag'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False)

    contacts = relationship('Contact', secondary=contact_tag, back_populates='tags')


class EmailTemplate(Base):
    __tablename__ = 'email_template'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    body_html = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    campaigns = relationship('Campaign', back_populates='template')


class Campaign(Base):
    __tablename__ = 'campaign'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    template_id = Column(Integer, ForeignKey('email_template.id'), nullable=True)
    status = Column(String, default='draft')  # draft, sending, paused, completed
    batch_size = Column(Integer, default=50)
    batch_delay = Column(Integer, default=60)
    total_sent = Column(Integer, default=0)
    total_opened = Column(Integer, default=0)
    total_clicked = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    template = relationship('EmailTemplate', back_populates='campaigns')
    campaign_contacts = relationship('CampaignContact', back_populates='campaign',
                                     cascade='all, delete-orphan')

    @property
    def total_recipients(self):
        return len(self.campaign_contacts)

    @property
    def pending_count(self):
        return sum(1 for cc in self.campaign_contacts if cc.status == 'pending')


class CampaignContact(Base):
    __tablename__ = 'campaign_contact'

    id = Column(Integer, primary_key=True, autoincrement=True)
    campaign_id = Column(Integer, ForeignKey('campaign.id', ondelete='CASCADE'), nullable=False)
    contact_id = Column(Integer, ForeignKey('contact.id', ondelete='CASCADE'), nullable=False)
    status = Column(String, default='pending')  # pending, sent, failed, retry, skipped
    sent_at = Column(DateTime, nullable=True)
    message_id = Column(String, nullable=True)
    retry_count = Column(Integer, default=0)

    campaign = relationship('Campaign', back_populates='campaign_contacts')
    contact = relationship('Contact', back_populates='campaign_contacts')
    tracking_events = relationship('TrackingEvent', back_populates='campaign_contact',
                                    cascade='all, delete-orphan')

    __table_args__ = (
        UniqueConstraint('campaign_id', 'contact_id', name='uq_campaign_contact'),
        Index('ix_cc_campaign', 'campaign_id'),
        Index('ix_cc_contact', 'contact_id'),
    )

    @property
    def was_opened(self):
        return any(e.event_type == 'open' for e in self.tracking_events)

    @property
    def was_clicked(self):
        return any(e.event_type == 'click' for e in self.tracking_events)

    @property
    def first_opened_at(self):
        opens = [e.created_at for e in self.tracking_events if e.event_type == 'open' and e.created_at]
        return min(opens) if opens else None

    @property
    def first_clicked_at(self):
        clicks = [e.created_at for e in self.tracking_events if e.event_type == 'click' and e.created_at]
        return min(clicks) if clicks else None


class TrackingEvent(Base):
    __tablename__ = 'tracking_event'

    id = Column(Integer, primary_key=True, autoincrement=True)
    campaign_contact_id = Column(Integer, ForeignKey('campaign_contact.id', ondelete='CASCADE'),
                                  nullable=False, index=True)
    event_type = Column(String, nullable=False)  # open, click
    url = Column(Text, nullable=True)
    ip_address = Column(String, nullable=True)
    user_agent = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    campaign_contact = relationship('CampaignContact', back_populates='tracking_events')


class DailySendLog(Base):
    __tablename__ = 'daily_send_log'

    date_str = Column(String, primary_key=True)  # e.g., '2026-04-02'
    count = Column(Integer, default=0)
