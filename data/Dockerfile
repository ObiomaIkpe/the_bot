FROM python:3.11-slim

# Install Python dependencies
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the builder scripts
COPY dxy_builder.py fx_builder.py ./

# All output (cache, chunks, final CSVs) goes to /data,
# which is mounted as a volume so it survives restarts.
WORKDIR /data

# Run dxy_builder first, then fx_builder.
# fx_builder reuses the cache dxy_builder already built,
# so EURUSD and GBPUSD won't be re-downloaded.
CMD ["bash", "-c", \
     "echo '━━━ STARTING dxy_builder ━━━' && \
      python /app/dxy_builder.py && \
      echo '━━━ STARTING fx_builder ━━━' && \
      python /app/fx_builder.py && \
      echo '━━━ ALL DONE ━━━'"]