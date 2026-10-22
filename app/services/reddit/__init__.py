# -*- coding: utf-8 -*-
"""Servicio de integración con Reddit (vía PRAW, síncrono)."""
from app.services.reddit.reddit_service import reddit_service, RedditService, HAS_PRAW

__all__ = ["reddit_service", "RedditService", "HAS_PRAW"]
