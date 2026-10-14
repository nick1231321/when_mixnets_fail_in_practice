#!/usr/bin/tcsh

set MIXCORR_DATA_PATH = `cat MIXCORR_DATA_PATH.txt`
set LOGS_PATH = ${MIXCORR_DATA_PATH}/logs
set DATABASES_PATH = `cat DATABASES_PATH.txt`
set DATABASE_ID = "baseline"
# set DATABASE_ID = "no-cover"
# set DATABASE_ID = "low-delay"
# set DATABASE_ID = "high-delay"
# set DATABASE_ID = "live-nym"

set CHUNKS_COUNT = 23
set CURRENT_CHUNK = 1

while ($CURRENT_CHUNK <= $CHUNKS_COUNT)
    set NEXT_CHUNK = `echo "$CURRENT_CHUNK + 1" | bc`
    echo -n "Launching chunk $NEXT_CHUNK/$CHUNKS_COUNT ... "
    echo "perform_experiment_real_data_alt_delay_characteristic_chunk($CURRENT_CHUNK, $CHUNKS_COUNT, '$DATABASES_PATH', '$MIXCORR_DATA_PATH', '$DATABASE_ID');" | octave >& $LOGS_PATH/${DATABASE_ID}_${CURRENT_CHUNK}_of_${CHUNKS_COUNT}.log &
    echo "Done."
    set CURRENT_CHUNK = $NEXT_CHUNK
end

echo "Check logs in $LOGS_PATH"
