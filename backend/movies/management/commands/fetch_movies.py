from django.core.management.base import BaseCommand

class Command(BaseCommand):
    help = "Fetch movies from TMDb and store with embeddings"

    def add_arguments(self, parser):
        parser.add_argument(
            "--pages",
            type=int,
            default=5,
            help="Number of pages to fetch (20 movies/page)",
        )
        parser.add_argument(
            "--list",
            type=str,
            default="popular",
            choices=["popular", "top_rated", "now_playing"],
            dest="list_type",
            help="Which TMDb list to fetch from",
        )
        parser.add_argument(
            "--skip-platforms",
            action="store_true",
            help="Skip syncing the streaming platform catalogue before fetching",
        )

    def handle(self, *args, **options):
        from movies.services.tmdb_client import (
            fetch_and_store_movies,
            fetch_streaming_platforms,
        )

        if not options["skip_platforms"]:
            self.stdout.write("Syncing streaming platforms...")
            fetch_streaming_platforms()

        pages = options["pages"]
        list_type = options["list_type"]
        self.stdout.write(f"Fetching {pages} pages from '{list_type}'...")
        saved_count = fetch_and_store_movies(pages=pages, list_type=list_type)

        self.stdout.write(
            self.style.SUCCESS(f"Done! Saved/filled {saved_count} movies.")
        )
