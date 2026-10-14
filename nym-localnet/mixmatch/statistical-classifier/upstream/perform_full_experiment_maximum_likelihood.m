
% The following parameters are the same for all the experiments:
% max_sequences_length is the number of payload packets analysed
max_sequences_length = 1000;
% sequences_count is the number of matched flows in the experiment
sequences_count = 1000;
% mu is the MixNet nodes delay parameter: Delay_mixnetnode ~ exp(mu)
mu = 1/0.050;
% lambda_payload is the payload packets rate
lambda_payload = 1/0.020;
% lambda_cover is the cover traffic packets rate
lambda_cover = 1/0.020;
% lambda_background_cover is the background cover traffic rate
lambda_background = 1/0.200;

fprintf(1, 'Experiment parameters:\n');
fprintf(1, '\tMaximum number of packets (including payload and cover traffic): %i\n', max_sequences_length); fflush(1);
fprintf(1, '\tSequences count: %i\n', sequences_count); fflush(1);
fprintf(1, '\tMixnet nodes rate (1/average_mixnet_delay) = %f\n', mu);
fprintf(1, '\tMixnet nodes average delay = %f\n', 1/mu);
fprintf(1, '\tPayload traffic process rate: %f\n', lambda_payload);
fprintf(1, '\tPayload traffic process average delay: %f\n', 1/lambda_payload);
fprintf(1, '\tCover traffic process rate: %f\n', lambda_cover);
fprintf(1, '\tCover traffic process average delay: %f\n', 1/lambda_cover);
fprintf(1, '\tBackground Cover traffic process rate: %f\n', lambda_background);
fprintf(1, '\tBackground Cover traffic process average delay: %f\n', 1/lambda_background);
fprintf(1, 'We assume that the number of cover packets needed will be at most twice the number of payload packets.\n');

% Since payload and cover traffic are both Poison processes, its combination is also a Poison process with rate lambda = lambda_payload + lambda_cover.
% We are only using this process, ignoring the underlying two distinct processes.
% Initial inbound times are generated assuming lambda = mu = 1. Then, for each lambda/mu ratio, all inbound and outbound
% times are recomputed (not regenerated, which is more computationally expensive).

% We start by generating all the inbound and outbound time sequences
if ~exist('Si_cover_matrix', 'var')
  fprintf(1, 'Generating %i inbound times sequences for cover traffic, of length %i, with lambda_cover = %f\n', sequences_count, max_sequences_length, lambda_cover); fflush(1);
  Si_cover_matrix = zeros(max_sequences_length, sequences_count);
  for k=1:sequences_count
    Si_cover_matrix(:,k) = generate_input_times_sequence(max_sequences_length, lambda_cover);
    fprintf(1, '\rProgress: %.2f%%', 100*k/sequences_count); fflush(1);
  endfor
  fprintf(1, '. Done!\n');
else
  fprintf(1, 'Skipping the generation of inbound times sequences for cover traffic.\n');
endif

if ~exist('Si_payload_matrix', 'var')
  fprintf(1, 'Generating %i inbound times sequences for payload traffic, of length %i, with lambda_payload = %f\n', sequences_count, max_sequences_length, lambda_payload); fflush(1);
  Si_payload_matrix = zeros(max_sequences_length, sequences_count);
  for k=1:sequences_count
    Si_payload_matrix(:,k) = generate_input_times_sequence(max_sequences_length, lambda_payload);
    fprintf(1, '\rProgress: %.2f%%', 100*k/sequences_count); fflush(1);
  endfor
  fprintf(1, '. Done!\n');
else
  fprintf(1, 'Skipping the generation of inbound times sequences for payload traffic.\n');
endif

if ~exist('So_payload_matrix', 'var')
  fprintf(1, 'Generating outbound times for payload traffic sequences by applying MixNet delays\n'); fflush(1);
  So_payload_matrix = zeros(size(Si_payload_matrix));
  for k=1:sequences_count
    So_payload_matrix(:,k) = apply_mixnet_delay(Si_payload_matrix(:,k), mu);
    fprintf(1, '\rProgress: %.2f%%', 100*k/sequences_count); fflush(1);
  endfor
  fprintf(1, '. Done!\n');
else
  fprintf(1, 'Skipping the generation of outbound times sequences for payload traffic.\n');
endif

if ~exist('So_cover_matrix', 'var')
  fprintf(1, 'Generating outbound times for cover traffic sequences by applying MixNet delays\n'); fflush(1);
  So_cover_matrix = zeros(size(Si_cover_matrix));
  for k=1:sequences_count
    So_cover_matrix(:,k) = apply_mixnet_delay(Si_cover_matrix(:,k), mu);
    fprintf(1, '\rProgress: %.2f%%', 100*k/sequences_count); fflush(1);
  endfor
  fprintf(1, '. Done!\n');
else
  fprintf(1, 'Skipping the generation of outbound times sequences for cover traffic.\n');
endif

if ~exist('So_cover_ack_matrix', 'var')
  fprintf(1, 'Generating outbound times sequences of cover traffic acknowledgements by applying MixNet delays\n'); fflush(1);
  So_cover_ack_matrix = zeros(size(So_cover_matrix));
  for k=1:sequences_count
    So_cover_ack_matrix(:,k) = apply_mixnet_delay(So_cover_matrix(:,k), mu);
    fprintf(1, '\rProgress: %.2f%%', 100*k/sequences_count); fflush(1);
  endfor
  fprintf(1, '. Done!\n');
else
  fprintf(1, 'Skipping the generation of outbound times sequences for cover traffic acknowlegements.\n');
endif

if ~exist('So_payload_ack_matrix', 'var')
  fprintf(1, 'Generating outbound times sequences of payload traffic acknowledgements by applying MixNet delays\n'); fflush(1);
  So_payload_ack_matrix = zeros(size(So_payload_matrix));
  for k=1:sequences_count
    So_payload_ack_matrix(:,k) = apply_mixnet_delay(So_payload_matrix(:,k), mu);
    fprintf(1, '\rProgress: %.2f%%', 100*k/sequences_count); fflush(1);
  endfor
  fprintf(1, '. Done!\n');
else
  fprintf(1, 'Skipping the generation of outbound times sequences for payload traffic acknowlegements.\n');
endif

if ~exist('Si_background_cover_client_matrix', 'var')
  fprintf(1, 'Generating %i inbound times sequences for background cover traffic from the client, of length %i, with lambda_background_cover = %f\n', sequences_count, max_sequences_length, lambda_background); fflush(1);
  Si_background_cover_client_matrix = zeros(max_sequences_length, sequences_count);
  for k=1:sequences_count
    Si_background_cover_client_matrix(:,k) = generate_input_times_sequence(max_sequences_length, lambda_background);
    fprintf(1, '\rProgress: %.2f%%', 100*k/sequences_count); fflush(1);
  endfor
  fprintf(1, '. Done!\n');
else
  fprintf(1, 'Skipping the generation of inbound times sequences for background cover traffic from the client.\n');
endif

if ~exist('Si_background_cover_server_matrix', 'var')
  fprintf(1, 'Generating %i inbound times sequences for background cover traffic from the server, of length %i, with lambda_background_cover = %f\n', sequences_count, max_sequences_length, lambda_background); fflush(1);
  Si_background_cover_server_matrix = zeros(max_sequences_length, sequences_count);
  for k=1:sequences_count
    Si_background_cover_server_matrix(:,k) = generate_input_times_sequence(max_sequences_length, lambda_background);
    fprintf(1, '\rProgress: %.2f%%', 100*k/sequences_count); fflush(1);
  endfor
  fprintf(1, '. Done!\n');
else
  fprintf(1, 'Skipping the generation of inbound times sequences for background cover traffic from the server.\n');
endif

if ~exist('So_background_cover_client_matrix', 'var')
  fprintf(1, 'Generating outbound times sequences of background cover traffic from the client by applying MixNet delays\n'); fflush(1);
  So_background_cover_client_matrix = zeros(size(Si_background_cover_client_matrix));
  for k=1:sequences_count
    So_background_cover_client_matrix(:,k) = apply_mixnet_delay(Si_background_cover_client_matrix(:,k), mu);
    fprintf(1, '\rProgress: %.2f%%', 100*k/sequences_count); fflush(1);
  endfor
  fprintf(1, '. Done!\n');
else
  fprintf(1, 'Skipping the generation of outbound times sequences for background cover traffic from the client.\n');
endif

if ~exist('So_background_cover_server_matrix', 'var')
  fprintf(1, 'Generating outbound times sequences of background cover traffic from the server by applying MixNet delays\n'); fflush(1);
  So_background_cover_server_matrix = zeros(size(Si_background_cover_server_matrix));
  for k=1:sequences_count
    So_background_cover_server_matrix(:,k) = apply_mixnet_delay(Si_background_cover_server_matrix(:,k), mu);
    fprintf(1, '\rProgress: %.2f%%', 100*k/sequences_count); fflush(1);
  endfor
  fprintf(1, '. Done!\n');
else
  fprintf(1, 'Skipping the generation of outbound times sequences for background cover traffic from the client.\n');
endif

if ~exist('So_background_cover_ack_client_matrix', 'var')
  fprintf(1, 'Generating outbound times sequences of background cover traffic acknowledgements from the client by applying MixNet delays\n'); fflush(1);
  So_background_cover_ack_client_matrix = zeros(size(So_background_cover_client_matrix));
  for k=1:sequences_count
    So_background_cover_ack_client_matrix(:,k) = apply_mixnet_delay(So_background_cover_client_matrix(:,k), mu);
    fprintf(1, '\rProgress: %.2f%%', 100*k/sequences_count); fflush(1);
  endfor
  fprintf(1, '. Done!\n');
else
  fprintf(1, 'Skipping the generation of outbound times sequences for background cover acknowledgments traffic from the client.\n');
endif

if ~exist('So_background_cover_ack_server_matrix', 'var')
  fprintf(1, 'Generating outbound times sequences of background cover traffic acknowledgements from the server by applying MixNet delays\n'); fflush(1);
  So_background_cover_ack_server_matrix = zeros(size(So_background_cover_server_matrix));
  for k=1:sequences_count
    So_background_cover_ack_server_matrix(:,k) = apply_mixnet_delay(So_background_cover_server_matrix(:,k), mu);
    fprintf(1, '\rProgress: %.2f%%', 100*k/sequences_count); fflush(1);
  endfor
  fprintf(1, '. Done!\n');
else
  fprintf(1, 'Skipping the generation of outbound times sequences for background cover acknowledgments traffic from the server.\n');
endif

if ~exist('loglikelihood_matrix', 'var')
  fprintf(1, 'Performing ALL vs ALL experiment.\n'); fflush(1);
  loglikelihood_matrix = zeros(sequences_count, sequences_count, max_sequences_length);
  for client_index = 1:sequences_count
    for server_index = 1:sequences_count
      % We need to mix payload and cover traffic processes
      inbound_traffic = sort([Si_cover_matrix(:,client_index); Si_payload_matrix(:,server_index); ...
			      Si_background_cover_client_matrix(:,client_index); Si_background_cover_server_matrix(:,server_index)], 'ascend');
      outbound_traffic = sort([So_cover_matrix(:,client_index); So_payload_matrix(:,client_index); ...
			       So_background_cover_client_matrix(:,client_index); So_background_cover_server_matrix(:,server_index)], 'ascend');
      outbound_acks = sort([So_cover_ack_matrix(:,client_index); So_payload_ack_matrix(:,server_index); ...
			    So_background_cover_ack_client_matrix(:,client_index); So_background_cover_ack_server_matrix(:,server_index)], 'ascend');
      loglikelihood_matrix(client_index, server_index, :) = ...
      simplified_sequences_match_loglikelihood(inbound_traffic(1:max_sequences_length), outbound_traffic(1:max_sequences_length), mu, true) + ...
      simplified_sequences_match_loglikelihood(outbound_traffic(1:max_sequences_length), outbound_acks(1:max_sequences_length), mu, true);
      fprintf(1, '\r\tProgress: %.2f%%', 100*((client_index - 1) + server_index/sequences_count)/sequences_count); fflush(1);
    endfor
  endfor
endif

EER = zeros(1, max_sequences_length);
FAR = zeros(1, max_sequences_length);
FRR = zeros(1, max_sequences_length);
threshold_EER = zeros(1, max_sequences_length);
impossible_false_matches_rate = zeros(1, max_sequences_length);
fprintf(1, '\nComputing attack performance...\n'); fflush(1);
false_scores_vector = zeros(1, sequences_count*(sequences_count-1));
true_scores_vector = zeros(1, sequences_count);
for sequence_length = 1:max_sequences_length
  for client_index = 1:sequences_count
    true_scores_vector(client_index) = (true_scores_vector(client_index)*(sequence_length - 1) + loglikelihood_matrix(client_index, client_index, sequence_length))/sequence_length;
    false_scores_vector(((client_index - 1)*(sequences_count - 1) + 1):(client_index*(sequences_count - 1))) = ...
    (false_scores_vector(((client_index - 1)*(sequences_count - 1) + 1):(client_index*(sequences_count - 1)))*(sequence_length - 1) + ...
     loglikelihood_matrix(client_index, 1:sequences_count ~= client_index, sequence_length))/sequence_length;
  endfor
  impossible_false_matches_rate(sequence_length) = sum(false_scores_vector == -Inf)/length(false_scores_vector);
  false_scores_vector(false_scores_vector == -Inf) = min(true_scores_vector) - 1000;
  [threshold_EER(sequence_length), FAR(sequence_length), FRR(sequence_length), EER(sequence_length)] = ...
  getThresholdEER(true_scores_vector', false_scores_vector');
  fprintf('\r\tProgress: %.2f%%', 100*sequence_length/max_sequences_length);
endfor
fprintf('\n');

figure;
plot(1:max_sequences_length, 100*EER);
grid minor;
xlabel('Total number of packets and their acks (client and server side)');
ylabel('Equal Error Rate (%)');
title(['\mu = ' num2str(mu) ', \lambda_{payload} = ' num2str(lambda_payload) '; \lambda_{cover} = ' num2str(lambda_cover) '; \lambda_{background\_cover} = ' num2str(lambda_background)])
print(['~/Experiments/MixCorr/EqualErrorRate_mu_' num2str(mu) '_lambda_payload_' num2str(lambda_payload) '_lambda_cover_' num2str(lambda_cover) '_lambda_background_cover_' num2str(lambda_background)], '-dpdfcrop')

figure;
plot(1:max_sequences_length, impossible_false_matches_rate);
grid minor;
xlabel('Total number of packets and their acks (client and server side)');
ylabel('Impossible False Matches Rate');
title(['\mu = ' num2str(mu) ', \lambda_{payload} = ' num2str(lambda_payload) '; \lambda_{cover} = ' num2str(lambda_cover) '; \lambda_{background\_cover} = ' num2str(lambda_background)])
print(['~/Experiments/MixCorr/ImpossibleFalseMatchesRate_mu_' num2str(mu) '_lambda_payload_' num2str(lambda_payload) '_lambda_cover_' num2str(lambda_cover) '_lambda_background_cover_' num2str(lambda_background)], '-dpdfcrop')

save('-binary', './full_experiment.oct');
