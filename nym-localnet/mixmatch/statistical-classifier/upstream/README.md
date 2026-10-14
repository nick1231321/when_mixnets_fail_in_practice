# Statistical Classifier for our PoPETs 2024.2 Paper "MixMatch"

Statistical classifier for our PoPETs 2024.2 paper "MixMatch: Flow Matching for Mixnet Traffic". MaximumLikelihood attack code (octave) and output (graphics). This repository uses input data from repositories `*_filtered-start-to-end`.

Primary author of this artifact: [Enrique Argones Rúa](https://www.esat.kuleuven.be/cosic/people/enrique-argones-rua/).

This repository is part of a larger list of repositories that we make available as artifacts of our paper. Please find more detailed documentation (including steps to set up the surrounding directories that this repository expects to be in place already) in our [main paper repository](https://github.com/mixnet-correlation/mixmatch-flow-matching-for-mixnet-traffic_popets-2024-2).

Please mind that running this artifact requires capable hardware and will take quite some time.


## Setting Up

Run the following steps to use this classifier:
```bash
root@ubuntu2204 $   apt-get install --yes octave octave-statistics tcsh tmux
root@ubuntu2204 $   mkdir -p ~/mixmatch/octave-packages
root@ubuntu2204 $   wget 'https://www.nist.gov/document/detwarev2-1-targz' -O ~/mixmatch/octave-packages/detware.tar.gz
root@ubuntu2204 $   cd ~/mixmatch/octave-packages
root@ubuntu2204 $   tar xvfz detware.tar.gz && rm -rf detware.tar.gz
root@ubuntu2204 $   chmod 0755 DETware_v2.1 && chown -R root:root DETware_v2.1 && chmod 0440 DETware_v2.1/*
root@ubuntu2204 $   printf "  addpath ('/root/mixmatch/octave-packages/DETware_v2.1', '-begin');\n" >> ~/.octaverc
root@ubuntu2204 $   cd ~/mixmatch
root@ubuntu2204 $   git clone https://github.com/mixnet-correlation/mixmatch_statistical_classifier.git
root@ubuntu2204 $   cd mixmatch_statistical_classifier
```


## Necessary Files and Edits

The file `MIXCORR_DATA_PATH.txt` must contain the desired path to the location of result files. A logs folder needs to be created in the path provided in `MIXCORR_DATA_PATH.txt`. The file `DATABASES_PATH.txt` must contain the path where the data data repositories are downloaded.

The following commands will take care of these requirements:
```bash
root@ubuntu2204 $   mkdir -p ~/mixmatch/{datasets,results}
root@ubuntu2204 $   mkdir -p ~/mixmatch/results/{logs,baseline,no-cover,low-delay,high-delay,two-to-one,live-nym}
root@ubuntu2204 $   cd ~/mixmatch/mixmatch_statistical_classifier
root@ubuntu2204 $   printf "~/mixmatch/results\n" > MIXCORR_DATA_PATH.txt
root@ubuntu2204 $   printf "~/mixmatch/datasets\n" > DATABASES_PATH.txt
```


## Analytical Experiment on Synthetic data

The main analytical experiment on synthetic data generated in octave is the script `perform_simplified_experiment.m`. It can be executed using the commands below or by executing octave in interactive mode and running `load('perform_simplified_experiment.m')`.
```bash
root@ubuntu2204 $   cd ~/mixmatch/mixmatch_statistical_classifier
root@ubuntu2204 $   tmux
root@ubuntu2204 $   octave perform_simplified_experiment.m
... Takes on the order of some hours to complete ...
```


## Analytical Experiment on Real Data from Nym

The other main experiment is for real data collected with Nym using a Gaussian net delay characteristic model in the Maximum Likelihood estimator. This data has to be preprocessed. This is done in the python module `real_data_experiment_parser.py` by the function `parse_real_data(databases_path, experiment_database)`. Then, the script `transform_flow_pair_lists.tcsh` has to be invoked, to transform the JSON lists defining the database partition into a list. Then, the experiment can be done by invoking the script `perform_experiment_real_data_alt_delay_characteristic.tcsh`. This script can be edited to modify the `database_id` and the number of chunks, which is the number of parallel octave instances used to perform the experiment. Outputs can then be processed by the Octave script `process_real_data_alt_delay_characteristic_experiment_results.m` to obtain the full ROC curves. These ROCs can be subsampled by using the script `subsample_TPR_FPR.m`.

Run the following commands:
```bash
root@ubuntu2204 $   cd ~/mixmatch/mixmatch_statistical_classifier
root@ubuntu2204 $   tmux
root@ubuntu2204 $   python real_data_experiment_parser.py
root@ubuntu2204 $   ./transform_flow_pair_lists.tcsh
root@ubuntu2204 $   ./perform_experiment_real_data_alt_delay_characteristic.tcsh
... Script returns immediately, but launches background tasks that take on the order of days to complete ...
root@ubuntu2204 $   octave
octave:1> process_real_data_alt_delay_characteristic_experiment_results("../results", "baseline", 23)
... Takes some time to complete ...
octave:1> exit
```

For the special case of the `two-to-one` experiment, you can replace the step of running `./perform_experiment_real_data_alt_delay_characteristic.tcsh` above with the following two commands:
```bash
root@ubuntu2204 $   cd ~/mixmatch/mixmatch_statistical_classifier
root@ubuntu2204 $   tmux
root@ubuntu2204 $   ./perform_experiment_real_data_alt_delay_characteristic_3parties_unmatched_negatives.tcsh
... Script returns immediately, but launches background tasks that take on the order of days to complete ...
root@ubuntu2204 $   ./perform_experiment_real_data_alt_delay_characteristic_3parties_semimatched_negatives.tcsh
... Script returns immediately, but launches background tasks that take on the order of days to complete ...
```


## Software Requirements

* Python
* octave
* octave statistics package
