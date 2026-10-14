#!/usr/bin/tcsh

set DATABASES_PATH = "../datasets"
set DATABASE_ID = "baseline"
# set DATABASE_ID = "no-cover"
# set DATABASE_ID = "low-delay"
# set DATABASE_ID = "high-delay"
# set DATABASE_ID = "live-nym"

foreach SET ("train" "val" "test")
    cat $DATABASES_PATH/$DATABASE_ID/flowpairs_${SET}.json | cut -d\" -f2 | grep _ > $DATABASES_PATH/$DATABASE_ID/flowpairs_${SET}.txt
end
