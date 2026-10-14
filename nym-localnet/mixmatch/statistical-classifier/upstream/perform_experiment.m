
max_sequences_length = 7;

% Uncomment if sequences are not preloaded.
% sequences_count = 1000;
% lambda = 1;
% fprintf(1, 'Generating input times sequences\n'); fflush(1);
% Si_matrix = zeros(max_sequences_length, sequences_count);
% for k=1:sequences_count
%   Si_matrix(:,k) = generate_input_times_sequence(max_sequences_length, lambda);
%   fprintf(1, '\rProgress: %.2f%%', 100*k/sequences_count); fflush(1);
% endfor
% fprintf(1, '. Done!\n');
% fprintf(1, 'Generating output times sequences by applying MixNet delays\n'); fflush(1);
% So_matrix = zeros(size(Si_matrix));
% for k=1:sequences_count
%   So_matrix(:,k) = apply_mixnet_delay(Si_matrix(:,k), lambda);
%   fprintf(1, '\rProgress: %.2f%%', 100*k/sequences_count); fflush(1);
% endfor
% fprintf(1, '. Done!\n');

sequences_length_vector = 1:7;
thr = [];
FAR = [];
FRR = [];
EER = [];
for sequences_length_index = 1:length(sequences_length_vector)
  sequences_length = sequences_length_vector(sequences_length_index);
  fprintf(1, 'Length %i sequences experiment\n', sequences_length);
  fprintf(1, '-----------------------------\n');
  
  fprintf(1, 'Computing loglikelihoods\n');
  
  true_matches_loglikelihoods_vector = [];
  false_matches_loglikelihoods_vector = [];
  loglikelihood_matrix = zeros(sequences_count, sequences_count);
  for Si_index = 1:sequences_count
    for So_index = 1:sequences_count
      loglikelihood_matrix(Si_index, So_index) = ...
      sequences_match_loglikelihood(Si_matrix(1:sequences_length,Si_index), So_matrix(1:sequences_length,So_index), lambda);
      if Si_index == So_index
	true_matches_loglikelihoods_vector = [true_matches_loglikelihoods_vector; loglikelihood_matrix(Si_index, So_index)];
      else
	false_matches_loglikelihoods_vector = [false_matches_loglikelihoods_vector; loglikelihood_matrix(Si_index, So_index)];
      endif
      fprintf(1, '\rProgress: %.2f%%', 100*((Si_index - 1) + So_index/sequences_count)/sequences_count); fflush(1);
    endfor
  endfor
  fprintf(1, '. Finished!\n');
  
  false_matches_loglikelihoods_vector(false_matches_loglikelihoods_vector == -Inf) = ...
  min(false_matches_loglikelihoods_vector(false_matches_loglikelihoods_vector ~= -Inf));
  [thr(sequences_length_index), FAR(sequences_length_index), FRR(sequences_length_index), EER(sequences_length_index)] = ...
  getThresholdEER(true_matches_loglikelihoods_vector, false_matches_loglikelihoods_vector);
  
  fprintf(1, 'Matching performance of experiment with sequences length %i:\n', sequences_length);
  fprintf(1, '\tFalse Match Rate:     %.2f%%\n', 100*FAR(sequences_length_index));
  fprintf(1, '\tFalse Non Match Rate: %.2f%%\n', 100*FRR(sequences_length_index));
  fprintf(1, '\tBalanced Accuracy:    %.2f%%\n', 50*((1-FAR(sequences_length_index)) + (1 - FRR(sequences_length_index))));
  
endfor
