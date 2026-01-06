"""KitesForU API client for E2E testing."""

import time
from typing import Optional

import requests

from ..config import get_config


class KitesForUClient:
    """Client for KitesForU API."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        """
        Initialize client.

        Args:
            base_url: API base URL (default from config)
            api_key: API key (default from config)
        """
        config = get_config()
        self.base_url = (base_url or config.kitesforu_api_url).rstrip('/')
        api_key = api_key or config.kitesforu_api_key

        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def create_job(
        self,
        topic: str,
        duration_min: int = 10,
        style: str = "Explainer",
        language: str = "en",
        **kwargs,
    ) -> dict:
        """
        Create a new podcast job.

        Args:
            topic: Podcast topic/request
            duration_min: Target duration in minutes
            style: Podcast style (Explainer, Storytelling, Interview, Motivational)
            language: Language code (not used by API, kept for CLI compatibility)
            **kwargs: Additional parameters

        Returns:
            Job data from API
        """
        # API CreateJobRequest fields: topic, duration_min, style, audience
        # Note: language is not supported by the API
        response = requests.post(
            f"{self.base_url}/v1/podcasts",
            headers=self.headers,
            json={
                "topic": topic,
                "duration_min": duration_min,
                "style": style,
                **kwargs,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def get_job(self, job_id: str) -> dict:
        """
        Get job status and details.

        Args:
            job_id: Job ID

        Returns:
            Job data from API
        """
        response = requests.get(
            f"{self.base_url}/v1/podcasts/{job_id}/status",
            headers=self.headers,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def wait_for_completion(
        self,
        job_id: str,
        timeout: int = 600,
        poll_interval: int = 10,
    ) -> dict:
        """
        Wait for job to complete.

        Args:
            job_id: Job ID
            timeout: Maximum wait time in seconds
            poll_interval: Time between status checks

        Returns:
            Completed job data

        Raises:
            TimeoutError: If job doesn't complete in time
        """
        start = time.time()

        while time.time() - start < timeout:
            job = self.get_job(job_id)
            status = job.get('status', '').lower()

            if status in ('completed', 'complete', 'done'):
                return job

            if status in ('failed', 'error', 'cancelled'):
                return job

            time.sleep(poll_interval)

        raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")

    def get_job_audio(self, job_id: str) -> Optional[str]:
        """
        Get audio URL for completed job.

        Args:
            job_id: Job ID

        Returns:
            Audio URL or None
        """
        job = self.get_job(job_id)
        return job.get('audio_url') or job.get('audio_path')

    def get_job_script(self, job_id: str) -> Optional[str]:
        """
        Get script for job.

        Args:
            job_id: Job ID

        Returns:
            Script text or None
        """
        job = self.get_job(job_id)
        return job.get('script') or job.get('script_text')

    def health_check(self) -> bool:
        """
        Check if API is healthy.

        Returns:
            True if API is responding
        """
        try:
            response = requests.get(
                f"{self.base_url}/v1/health",
                timeout=5,
            )
            return response.status_code == 200
        except Exception:
            return False
