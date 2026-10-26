#!/bin/bash
# Print the epoch time once the screen stops changing (two equal tiny frames), max ~3 s.
prev=""
for i in $(seq 15); do
  cur=$(ffmpeg -loglevel error -f x11grab -video_size ${CF_W:-1280}x${CF_H:-720} -i :99 \
        -frames:v 1 -vf scale=160:90 -f rawvideo - 2>/dev/null | md5sum | cut -c1-32)
  [ "$cur" = "$prev" ] && break
  prev=$cur; sleep 0.2
done
date +%s.%N
